from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('learning', '0005_progression_and_interest'),
    ]

    operations = [
        migrations.AddField(
            model_name='topicprogress',
            name='resource_links_viewed_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
