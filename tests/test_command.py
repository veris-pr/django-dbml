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
    assert "Table testapp_author {" in output
    assert "Table testapp_authorprofile {" in output
    assert "Table testapp_book {" in output
    assert "Table testapp_tag {" in output
    assert "Table testapp_shelf {" in output
    assert "Table testapp_book_tags {" in output
    assert "ref: testapp_book.author_id > testapp_author.id" in output
    assert "ref: testapp_authorprofile.author_id - testapp_author.id" in output
    assert "ref: testapp_book_tags.book_id > testapp_book.id" in output
    assert "ref: testapp_book_tags.tag_id > testapp_tag.id" in output
    assert "Stores books" in output
    assert "Shown in catalogs" in output
    assert "EmailConfirmation(id, email_address, created, sent, key)" not in output
    assert "(author_id) [name: 'testapp_book_author_id', type: btree]" in output
    assert "(name) [unique, name: 'testapp_author_name_uniq', type: btree]" in output


def test_dbml_command_prefers_physical_schema_details() -> None:
    output = render_dbml(disable_update_timestamp=True)

    assert "Table testapp.Book {" not in output
    assert "Table testapp_book {" in output
    assert "default:`" not in output
    assert "id integer [pk, increment, not null]" in output
    assert "name varchar(100) [note: '''Shown in catalogs''', unique, not null]" in output
    assert "website_url varchar(200) [not null]" in output
    assert "metadata text [not null]" in output
    assert "status varchar(16)" in output
    assert "author_id bigint" in output
    assert "book_id bigint" in output
    assert "tag_id bigint" in output
    assert "checks {" in output
    assert '`(JSON_VALID("metadata") OR "metadata" IS NULL)` [name: \'testapp_author_metadata_check\']' in output
    assert '`"position" >= 0` [name: \'testapp_shelf_position_check\']' in output
    assert '`"position" >= 1` [name: \'testapp_shelf_position_gte_1\']' in output
    assert "*title*" not in output
    assert "Visible title" not in output
    assert "Draft" not in output


def test_dbml_command_can_use_model_labels() -> None:
    output = render_dbml(disable_update_timestamp=True, table_names=False)

    assert "Table testapp.Book {" in output
    assert "ref: testapp.Book.author_id > testapp.Author.id" in output
    assert "Table testapp_book {" not in output


def test_dbml_command_includes_related_models_for_specific_model_selection() -> None:
    output = render_dbml(
        "testapp.Book",
        add_project_name="Library",
        add_project_notes="Generated for tests.",
        disable_update_timestamp=True,
    )

    assert "Table testapp_book {" in output
    assert "Table testapp_author {" in output
    assert "Table testapp_tag {" in output
    assert "Table testapp_book_tags {" in output
    assert "Table testapp_authorprofile {" not in output


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
    assert "Table testapp_book {" in output
