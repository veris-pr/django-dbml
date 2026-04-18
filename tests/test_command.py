from io import StringIO

import pytest
from django.core.management import call_command


pytestmark = pytest.mark.django_db


def render_dbml(*app_labels: str, **options: object) -> str:
    buffer = StringIO()
    call_command("dbml", *app_labels, stdout=buffer, **options)
    return buffer.getvalue()


def test_dbml_command_generates_project_block_and_relations() -> None:
    output = render_dbml(
        add_project_name="Library",
        add_project_notes="Generated for tests.",
        disable_update_timestamp=True,
    )

    assert 'Project "Library"' in output
    assert "Table testapp.Author {" in output
    assert "Table testapp.AuthorProfile {" in output
    assert "Table testapp.Book {" in output
    assert "Table testapp.Tag {" in output
    assert "Table testapp.Shelf {" in output
    assert "Table testapp.book_tags {" in output
    assert "ref: testapp.Book.author_id > testapp.Author.id" in output
    assert "ref: testapp.AuthorProfile.author_id - testapp.Author.id" in output
    assert "ref: testapp.book_tags.book_id > testapp.Book.id" in output
    assert "ref: testapp.book_tags.tag_id > testapp.Tag.id" in output
    assert "*DB comment: Stores books*" in output
    assert "Shown in catalogs" in output
    assert "Draft" in output


def test_dbml_command_prefers_physical_schema_details() -> None:
    output = render_dbml(disable_update_timestamp=True)

    assert "enum testapp.char_book_status {" not in output
    assert "default:`" not in output
    assert "id integer [note: '''*ID*''', pk, increment, not null]" in output
    assert "name varchar(100)" in output
    assert "website_url varchar(200) [note: '''*website url*''', not null]" in output
    assert "metadata text [note: '''" in output
    assert "status varchar(16)" in output
    assert "author_id bigint" in output
    assert "book_id bigint" in output
    assert "tag_id bigint" in output
    assert "checks {" in output
    assert '`(JSON_VALID("metadata") OR "metadata" IS NULL)` [name: \'testapp_author_metadata_check\']' in output
    assert '`"position" >= 0` [name: \'testapp_shelf_position_check\']' in output
    assert '`"position" >= 1` [name: \'testapp_shelf_position_gte_1\']' in output


def test_dbml_command_includes_related_models_for_specific_model_selection() -> None:
    output = render_dbml(
        "testapp.Book",
        add_project_name="Library",
        add_project_notes="Generated for tests.",
        disable_update_timestamp=True,
    )

    assert "Table testapp.Book {" in output
    assert "Table testapp.Author {" in output
    assert "Table testapp.Tag {" in output
    assert "Table testapp.book_tags {" in output
    assert "Table testapp.AuthorProfile {" not in output


def test_dbml_command_writes_to_output_file(tmp_path) -> None:
    output_file = tmp_path / "schema.dbml"

    call_command(
        "dbml",
        add_project_name="Library",
        add_project_notes="Generated for tests.",
        disable_update_timestamp=True,
        output_file=str(output_file),
    )

    output = output_file.read_text(encoding="utf-8")

    assert output.startswith('Project "Library" {')
    assert "Table testapp.Book {" in output
