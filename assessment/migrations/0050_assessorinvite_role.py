from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('assessment', '0049_assessor_invite'),
    ]

    operations = [
        migrations.AddField(
            model_name='assessorinvite',
            name='role',
            field=models.CharField(
                choices=[('assessor', 'Assessor'), ('moderator', 'Moderator')],
                default='assessor',
                max_length=20,
            ),
        ),
    ]
