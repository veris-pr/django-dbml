# ruff: noqa: SLF001
import hashlib
from functools import cache

from django.conf import settings
from django.contrib.postgres.indexes import HashIndex
from django.db import connection, models
from django.db.models import Model
from django.db.models.fields import Field

from django_dbml.core.options import GenerationOptions
from django_dbml.core.schema import CheckDefinition, FieldDefinition, IndexDefinition, ProjectDefinition, RelationDefinition, TableDefinition
from django_dbml.core.selection import get_model_group, select_models
from django_dbml.utils import clean_db_identifier, simplify_generated_name, to_snake_case


class SchemaBuilder:
    def __init__(self, options: GenerationOptions) -> None:
        self.options = options

    def build(self, app_labels: tuple[str, ...]) -> ProjectDefinition:
        project = ProjectDefinition(
            name=self.options.project_name,
            notes=self.options.project_notes,
            database_type=self.get_db_type(),
            include_timestamp=not self.options.disable_update_timestamp,
        )

        for model in select_models(app_labels):
            self._build_table(project, model)

        return project

    def _build_table(self, project: ProjectDefinition, model: type[Model]) -> None:
        group_name = get_model_group(model)
        table_name = self.get_table_name(model)
        table = TableDefinition(
            name=table_name,
            group=group_name,
            color=self.get_table_color(group_name),
        )
        project.tables[table_name] = table

        for field in model._meta.local_fields:
            column_name = field.column
            table.fields[column_name] = self.build_field_definition(field)
            self.add_field_index(table, model, field, column_name)

            if not getattr(field, "db_constraint", True):
                continue

            if isinstance(field, models.fields.related.OneToOneField):
                table.relations.append(
                    RelationDefinition(
                        kind="one_to_one",
                        table_from=self.get_table_name(field.related_model),
                        table_from_field=field.target_field.column,
                        table_to=table_name,
                        table_to_field=column_name,
                    )
                )
                continue

            if isinstance(field, models.fields.related.ForeignKey):
                table.relations.append(
                    RelationDefinition(
                        kind="one_to_many",
                        table_from=self.get_table_name(field.related_model),
                        table_from_field=field.target_field.column,
                        table_to=table_name,
                        table_to_field=column_name,
                    )
                )

        for field in model._meta.local_many_to_many:
            self._build_many_to_many_table(project, field, group_name)

        self.add_checks(table, model)
        self.add_meta_indexes(table, model)
        self.add_table_notes(table, model)

    def _build_many_to_many_table(self, project: ProjectDefinition, field: Field, group_name: str) -> None:
        through_model = field.remote_field.through
        if not through_model._meta.auto_created:
            return

        original_table_name = field.m2m_db_table()
        table_name = self.qualify_table_name(original_table_name)
        if not self.options.table_names and "_" in original_table_name:
            table_name = original_table_name.replace("_", ".", 1)
        if table_name in project.tables:
            return

        table = TableDefinition(
            name=table_name,
            group=group_name,
            color=self.get_table_color(group_name),
        )

        for through_field in through_model._meta.local_fields:
            column_name = through_field.column
            table.fields[column_name] = self.build_field_definition(through_field)
            self.add_field_index(table, through_model, through_field, column_name)

            if not getattr(through_field, "db_constraint", True):
                continue

            if isinstance(through_field, models.fields.related.OneToOneField):
                table.relations.append(
                    RelationDefinition(
                        kind="one_to_one",
                        table_from=self.get_table_name(through_field.related_model),
                        table_from_field=through_field.target_field.column,
                        table_to=table_name,
                        table_to_field=column_name,
                    )
                )
                continue

            if isinstance(through_field, models.fields.related.ForeignKey):
                table.relations.append(
                    RelationDefinition(
                        kind="one_to_many",
                        table_from=self.get_table_name(through_field.related_model),
                        table_from_field=through_field.target_field.column,
                        table_to=table_name,
                        table_to_field=column_name,
                    )
                )

        self.add_checks(table, through_model)
        self.add_meta_indexes(table, through_model)

        project.tables[table_name] = table

    def build_field_definition(self, field: Field) -> FieldDefinition:
        field_definition = FieldDefinition(type=self.get_dbml_field_type(field))

        if getattr(field, "db_comment", ""):
            field_definition.note = field.db_comment.replace('"', '\\"')

        if getattr(field, "null", False):
            field_definition.null = True

        if getattr(field, "primary_key", False):
            field_definition.pk = True

        if getattr(field, "unique", False) and not field_definition.pk:
            field_definition.unique = True

        if field.get_internal_type() in {"AutoField", "BigAutoField", "SmallAutoField"}:
            field_definition.increment = True

        return field_definition

    def add_field_index(self, table: TableDefinition, model: type[Model], field: Field, field_name: str) -> None:
        if not (getattr(field, "db_index", False) or getattr(field, "primary_key", False) or getattr(field, "unique", False)):
            return

        if field.primary_key:
            index_name = f"{model._meta.db_table}_pkey"
        elif isinstance(field, models.fields.related.OneToOneField) or field.unique:
            index_name = connection.schema_editor()._unique_constraint_name(model._meta.db_table, [field_name], quote=False)
        else:
            index_name = connection.schema_editor()._create_index_name(model._meta.db_table, [field_name])
        index_name = simplify_generated_name(index_name)

        table.indexes.append(
            IndexDefinition(
                fields=[field_name],
                type="btree",
                name=index_name,
                unique=field.unique and not field.primary_key,
                pk=field.primary_key,
            )
        )

    def add_meta_indexes(self, table: TableDefinition, model: type[Model]) -> None:
        for index in model._meta.indexes:
            column_names = [model._meta._forward_fields_map[field_name].column for field_name in index.fields]
            table.indexes.append(
                IndexDefinition(
                    fields=column_names,
                    type="hash" if isinstance(index, HashIndex) else "btree",
                    name=index.name,
                )
            )

        for unique_together in model._meta.unique_together:
            column_names = [model._meta._forward_fields_map[field_name].column for field_name in unique_together]
            table.indexes.append(
                IndexDefinition(
                    fields=column_names,
                    type="btree",
                    name=simplify_generated_name(
                        connection.schema_editor()._unique_constraint_name(model._meta.db_table, column_names, quote=False)
                    ),
                    unique=True,
                )
            )

    def add_checks(self, table: TableDefinition, model: type[Model]) -> None:
        if not connection.features.supports_table_check_constraints:
            return

        schema_editor = connection.schema_editor()

        for field in model._meta.local_fields:
            check_expression = field.db_parameters(connection).get("check")
            if not check_expression:
                continue

            table.checks.append(
                CheckDefinition(
                    expression=check_expression,
                    name=f"{model._meta.db_table}_{field.column}_check",
                )
            )

        for constraint in model._meta.constraints:
            if not isinstance(constraint, models.CheckConstraint):
                continue

            table.checks.append(
                CheckDefinition(
                    expression=constraint._get_check_sql(model, schema_editor),
                    name=constraint.name,
                )
            )

    def add_table_notes(self, table: TableDefinition, model: type[Model]) -> None:
        if getattr(model._meta, "db_table_comment", ""):
            table.note = model._meta.db_table_comment.replace('"', '\\"')

    def get_table_name(self, model: type[Model]) -> str:
        if self.options.table_names:
            return self.qualify_table_name(model._meta.db_table)
        return model._meta.label

    def get_table_color(self, group_name: str) -> str:
        if not self.options.color_by_app:
            return ""
        return f"#{hashlib.sha256(group_name.encode()).hexdigest()[:6]}"

    def get_db_type(self) -> str:
        db = settings.DATABASES["default"]
        engine = db["ENGINE"].lower()

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

        return f"Unknown ({db['ENGINE']})"

    def qualify_table_name(self, table_name: str) -> str:
        return clean_db_identifier(table_name)

    def get_dbml_field_type(self, field: Field) -> str:
        db_parameters = field.db_parameters(connection)
        db_type = db_parameters.get("type") or field.db_type(connection)
        if not db_type:
            db_type = map_field_type_to_dbml_type(type(field))

        if any(character.isspace() for character in db_type):
            return f'"{db_type}"'

        return db_type


@cache
def map_field_type_to_dbml_type(field: type[Field]) -> str:
    """Given a field class, return the DBML type used in the schema."""

    return to_snake_case(field.__name__.removesuffix("Field"))
