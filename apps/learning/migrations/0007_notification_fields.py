from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('learning', '0006_quiz_resource_links_viewed'),
    ]

    operations = [
        migrations.AddField(
            model_name='usersubjectprogress',
            name='notification_frequency_hours',
            field=models.PositiveSmallIntegerField(default=24),
        ),
        migrations.AddField(
            model_name='usersubjectprogress',
            name='next_due_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
