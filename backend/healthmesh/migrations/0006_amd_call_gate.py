"""Shared Token Factory admission lease, with no credential or prompt data."""
from datetime import datetime, timezone

from django.db import migrations, models


def seed_gate(apps, schema_editor):
    gate = apps.get_model('healthmesh', 'AMDCallGate')
    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    gate.objects.using(schema_editor.connection.alias).create(
        scope='token_factory', lease_until=epoch, next_allowed_at=epoch)


class Migration(migrations.Migration):
    dependencies = [('healthmesh', '0005_ai_extraction_provenance')]

    operations = [
        migrations.CreateModel(
            name='AMDCallGate',
            fields=[
                ('scope', models.CharField(max_length=40, primary_key=True, serialize=False)),
                ('owner_token', models.UUIDField(blank=True, null=True)),
                ('lease_until', models.DateTimeField()),
                ('next_allowed_at', models.DateTimeField()),
            ],
        ),
        migrations.RunPython(seed_gate, migrations.RunPython.noop),
    ]
