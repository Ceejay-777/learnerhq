from django.db import migrations


class Migration(migrations.Migration):

    dependencies: list[tuple[str, str]] = []

    operations = [
        migrations.RunSQL(
            sql='CREATE EXTENSION IF NOT EXISTS vector',
            reverse_sql='DROP EXTENSION IF EXISTS vector',
        ),
    ]
