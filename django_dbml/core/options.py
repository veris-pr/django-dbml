from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class GenerationOptions:
    table_names: bool = True
    database: str | None = None
    group_by_app: bool = False
    color_by_app: bool = False
    add_project_name: str | None = None
    add_project_notes: str | None = None
    disable_update_timestamp: bool = False
    output_file: Path | None = None

    @property
    def project_name(self) -> str:
        return self.add_project_name or "Django DBML"

    @property
    def project_notes(self) -> str:
        if self.add_project_notes:
            return self.add_project_notes
        if self.database:
            return f"Generated from Django database '{self.database}'."
        return "Generated from Django models."

    @classmethod
    def from_command_kwargs(cls, kwargs: dict) -> "GenerationOptions":
        output_file = kwargs.get("output_file")
        return cls(
            table_names=kwargs.get("table_names", True),
            database=kwargs.get("database"),
            group_by_app=kwargs.get("group_by_app", False),
            color_by_app=kwargs.get("color_by_app", False),
            add_project_name=kwargs.get("add_project_name"),
            add_project_notes=kwargs.get("add_project_notes"),
            disable_update_timestamp=kwargs.get("disable_update_timestamp", False),
            output_file=Path(output_file) if output_file else None,
        )
