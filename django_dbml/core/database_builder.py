from django.apps import apps
from django.core.management.base import CommandError
from django.db import DEFAULT_DB_ALIAS, connections
from django.db.models import Model
from django.utils.connection import ConnectionDoesNotExist

from django_dbml.core.options import GenerationOptions
from django_dbml.core.schema import FieldDefinition, IndexDefinition, ProjectDefinition, RelationDefinition, TableDefinition
from django_dbml.core.selection import get_model_group, normalize_model, select_models
from django_dbml.utils import clean_db_identifier, simplify_generated_name, to_snake_case


class DatabaseSchemaBuilder:
    def __init__(self, options: GenerationOptions) -> None:
        self.options = options
        self.database_alias = options.database or DEFAULT_DB_ALIAS
        try:
            self.connection = connections[self.database_alias]
        except ConnectionDoesNotExist as exc:
            raise CommandError(f"Database alias '{self.database_alias}' is not configured.") from exc
        self.models_by_table = self._build_models_by_table()

    def build(self, app_labels: tuple[str, ...]) -> ProjectDefinition:
        project = ProjectDefinition(
            name=self.options.project_name,
            notes=self.options.project_notes,
            database_type=self.get_db_type(),
            include_timestamp=not self.options.disable_update_timestamp,
        )

        selected_tables = self.get_selected_table_names(app_labels)
        with self.connection.cursor() as cursor:
            available_tables = {
                table_info.name: table_info
                for table_info in self.connection.introspection.get_table_list(cursor)
                if getattr(table_info, "type", "t") in {"t", "p"}
            }
            missing_tables = sorted(selected_tables - set(available_tables))
            if missing_tables:
                missing_tables_list = ", ".join(missing_tables)
                raise CommandError(
                    f"Tables not found in database '{self.database_alias}': {missing_tables_list}"
                )

            for table_name in sorted(selected_tables):
                self._build_table(project, table_name, available_tables[table_name], cursor)

        return project

    def _build_table(self, project: ProjectDefinition, db_table_name: str, table_info, cursor) -> None:
        table_name = self.get_table_name(db_table_name)
        group_name = self.get_table_group(db_table_name)
        table = TableDefinition(
            name=table_name,
            group=group_name,
            color=self.get_table_color(group_name),
        )

        project.tables[table_name] = table

        constraints = self.connection.introspection.get_constraints(cursor, db_table_name)
        descriptions = self.connection.introspection.get_table_description(cursor, db_table_name)
        column_types = self.get_column_types(cursor, db_table_name, descriptions)
        primary_key_columns = self.get_primary_key_columns(constraints)
        single_unique_columns = self.get_single_unique_columns(constraints)

        for description in descriptions:
            column_name = description.name
            table.fields[column_name] = self.build_field_definition(
                description=description,
                db_type=column_types.get(column_name),
                primary_key_columns=primary_key_columns,
                single_unique_columns=single_unique_columns,
            )

        table.relations.extend(self.build_relations(db_table_name, table_name, constraints))
        table.indexes.extend(self.build_indexes(db_table_name, constraints))

        table_comment = getattr(table_info, "comment", None)
        if table_comment:
            table.note = table_comment

    def build_field_definition(
        self,
        *,
        description,
        db_type: str | None,
        primary_key_columns: set[str],
        single_unique_columns: set[str],
    ) -> FieldDefinition:
        column_name = description.name
        field_definition = FieldDefinition(
            type=self.get_dbml_type(db_type, description),
            null=bool(description.null_ok),
            pk=column_name in primary_key_columns and len(primary_key_columns) == 1,
            unique=column_name in single_unique_columns and column_name not in primary_key_columns,
            increment=self.is_incrementing_column(description),
        )

        column_comment = getattr(description, "comment", None)
        if column_comment:
            field_definition.note = column_comment

        return field_definition

    def build_relations(self, db_table_name: str, rendered_table_name: str, constraints: dict) -> list[RelationDefinition]:
        relations: list[RelationDefinition] = []

        for constraint in constraints.values():
            columns = constraint.get("columns") or []
            foreign_key = constraint.get("foreign_key")
            if foreign_key is None or len(columns) != 1:
                continue

            related_table_name, related_column_name = foreign_key
            relation_kind = "one_to_one" if self.has_unique_constraint(constraints, columns) else "one_to_many"
            relations.append(
                RelationDefinition(
                    kind=relation_kind,
                    table_from=self.get_table_name(related_table_name),
                    table_from_field=related_column_name,
                    table_to=rendered_table_name,
                    table_to_field=columns[0],
                )
            )

        return relations

    def build_indexes(self, db_table_name: str, constraints: dict) -> list[IndexDefinition]:
        indexes: list[IndexDefinition] = []

        for constraint_name, constraint in sorted(constraints.items()):
            columns = constraint.get("columns") or []
            if constraint.get("foreign_key") or not columns:
                continue

            if not (constraint.get("primary_key") or constraint.get("index") or constraint.get("unique")):
                continue

            if constraint.get("primary_key"):
                index_name = f"{db_table_name}_pkey"
            elif self.is_unnamed_unique_constraint(constraint_name, constraint):
                index_name = f"{db_table_name}_{'_'.join(columns)}_uniq"
            else:
                index_name = simplify_generated_name(constraint_name)

            indexes.append(
                IndexDefinition(
                    fields=columns,
                    type=self.get_index_type(constraint),
                    name=index_name,
                    unique=bool(constraint.get("unique")) and not bool(constraint.get("primary_key")),
                    pk=bool(constraint.get("primary_key")),
                )
            )

        return indexes

    def get_selected_table_names(self, app_labels: tuple[str, ...]) -> set[str]:
        selected_tables: set[str] = set()

        for model in select_models(app_labels):
            concrete_model = normalize_model(model)
            selected_tables.add(concrete_model._meta.db_table)

            for field in concrete_model._meta.local_many_to_many:
                through_model = field.remote_field.through
                if through_model is not None and through_model._meta.auto_created:
                    selected_tables.add(field.m2m_db_table())

        return selected_tables

    def get_primary_key_columns(self, constraints: dict) -> set[str]:
        for constraint in constraints.values():
            if constraint.get("primary_key"):
                return set(constraint.get("columns") or [])
        return set()

    def get_single_unique_columns(self, constraints: dict) -> set[str]:
        unique_columns: set[str] = set()
        for constraint in constraints.values():
            columns = constraint.get("columns") or []
            if constraint.get("primary_key") or len(columns) != 1:
                continue
            if constraint.get("unique"):
                unique_columns.add(columns[0])
        return unique_columns

    def has_unique_constraint(self, constraints: dict, columns: list[str]) -> bool:
        for constraint in constraints.values():
            if (constraint.get("columns") or []) != columns:
                continue
            if constraint.get("primary_key") or constraint.get("unique"):
                return True
        return False

    def is_unnamed_unique_constraint(self, constraint_name: str, constraint: dict) -> bool:
        if not constraint.get("unique") or constraint.get("primary_key"):
            return False
        return constraint_name.startswith("__unnamed_constraint_") or constraint_name.startswith("sqlite_autoindex_")

    def get_index_type(self, constraint: dict) -> str:
        index_type = constraint.get("type")
        if index_type in {None, "", "idx", "btree"}:
            return "btree"
        return str(index_type)

    def get_db_type(self) -> str:
        engine = self.connection.settings_dict["ENGINE"].lower()

        if "postgres" in engine:
            return "PostgreSQL"
        if "sqlite" in engine:
            return "SQLite"
        if "mysql" in engine:
            return "MySQL"
        if "oracle" in engine:
            return "Oracle"
        if "mssql" in engine:
            return "Microsoft SQL"

        return f"Unknown ({self.connection.settings_dict['ENGINE']})"

    def get_table_name(self, db_table_name: str) -> str:
        if self.options.table_names:
            return clean_db_identifier(db_table_name)

        model = self.models_by_table.get(db_table_name)
        if model is None or model._meta.auto_created:
            return clean_db_identifier(db_table_name)
        return model._meta.label

    def get_table_group(self, db_table_name: str) -> str:
        model = self.models_by_table.get(db_table_name)
        if model is not None:
            return get_model_group(model)
        return db_table_name.partition("_")[0]

    def get_table_color(self, group_name: str) -> str:
        if not self.options.color_by_app:
            return ""
        import hashlib

        return f"#{hashlib.sha256(group_name.encode()).hexdigest()[:6]}"

    def get_dbml_type(self, db_type: str | None, description) -> str:
        normalized_db_type = (db_type or "").lower()
        if not normalized_db_type:
            field_type = self.connection.introspection.get_field_type(description.type_code, description)
            normalized_db_type = to_snake_case(field_type.removesuffix("Field"))
        if any(character.isspace() for character in normalized_db_type):
            return f'"{normalized_db_type}"'
        return normalized_db_type

    def get_column_types(self, cursor, table_name: str, descriptions: list) -> dict[str, str]:
        if self.connection.vendor == "sqlite":
            return {description.name: str(description.type_code).lower() for description in descriptions}

        if self.connection.vendor == "postgresql":
            cursor.execute(
                """
                SELECT a.attname, format_type(a.atttypid, a.atttypmod)
                FROM pg_attribute a
                JOIN pg_class c ON a.attrelid = c.oid
                JOIN pg_namespace n ON c.relnamespace = n.oid
                WHERE c.relname = %s
                    AND a.attnum > 0
                    AND NOT a.attisdropped
                    AND n.nspname NOT IN ('pg_catalog', 'pg_toast')
                    AND pg_catalog.pg_table_is_visible(c.oid)
                """,
                [table_name],
            )
            return {column_name: db_type for column_name, db_type in cursor.fetchall()}

        return {description.name: str(description.type_code).lower() for description in descriptions}

    def is_incrementing_column(self, description) -> bool:
        if getattr(description, "is_autofield", False):
            return True

        column_default = getattr(description, "default", None)
        if isinstance(column_default, str):
            lowered_default = column_default.lower()
            if "nextval(" in lowered_default or "generated by default as identity" in lowered_default:
                return True

        return bool(getattr(description, "pk", False) and str(description.type_code).upper() == "INTEGER")

    def _build_models_by_table(self) -> dict[str, type[Model]]:
        models_by_table: dict[str, type[Model]] = {}

        for model in apps.get_models():
            concrete_model = normalize_model(model)
            models_by_table.setdefault(concrete_model._meta.db_table, concrete_model)

        return models_by_table
