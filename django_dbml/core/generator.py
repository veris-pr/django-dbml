from django_dbml.core.database_builder import DatabaseSchemaBuilder
from django_dbml.core.builder import SchemaBuilder
from django_dbml.core.options import GenerationOptions
from django_dbml.core.renderer import DbmlRenderer


def generate_dbml(app_labels: tuple[str, ...], options: GenerationOptions) -> str:
    builder = DatabaseSchemaBuilder(options) if options.database else SchemaBuilder(options)
    project = builder.build(app_labels)
    return DbmlRenderer(options).render(project)
