from django.db import migrations, models


def mark_existing_roadmaps_ready(apps, schema_editor):
    Subject = apps.get_model("learning", "Subject")
    Subject.objects.filter(topics__isnull=False).update(roadmap_status="READY")


class Migration(migrations.Migration):

    dependencies = [
        ("learning", "0009_alter_subject_embedding"),
    ]

    operations = [
        migrations.AddField(
            model_name="subject",
            name="roadmap_status",
            field=models.CharField(
                choices=[
                    ("NOT_GENERATED", "Not Generated"),
                    ("GENERATING", "Generating"),
                    ("READY", "Ready"),
                    ("FAILED", "Failed"),
                ],
                default="NOT_GENERATED",
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="subject",
            name="roadmap_started_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.RunPython(mark_existing_roadmaps_ready, migrations.RunPython.noop),
    ]
