from django.db import migrations


def install_constraint(apps, schema_editor):
    if schema_editor.connection.vendor == "postgresql":
        schema_editor.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")
        schema_editor.execute("""
            ALTER TABLE booking_appointment
            ADD CONSTRAINT barber_appointment_no_overlap
            EXCLUDE USING GIST (barber_id WITH =, tstzrange(start, "end", '[)') WITH &&)
            WHERE (status IN ('confirmed', 'attended', 'no_show'))
        """)


def remove_constraint(apps, schema_editor):
    if schema_editor.connection.vendor == "postgresql":
        schema_editor.execute(
            "ALTER TABLE booking_appointment DROP CONSTRAINT barber_appointment_no_overlap"
        )


class Migration(migrations.Migration):
    dependencies = [("booking", "0001_initial")]
    operations = [migrations.RunPython(install_constraint, remove_constraint)]
