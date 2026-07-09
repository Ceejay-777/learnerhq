from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('learning', '0002_subject_embedding'),
    ]

    operations = [
        migrations.AddField(
            model_name='topic',
            name='generation_started_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
