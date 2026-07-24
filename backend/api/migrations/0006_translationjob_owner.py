from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("api", "0005_translationjob_started_at")]

    operations = [
        migrations.AddField(
            model_name="translationjob",
            name="owner",
            field=models.CharField(db_index=True, default="default", max_length=96),
        ),
    ]
