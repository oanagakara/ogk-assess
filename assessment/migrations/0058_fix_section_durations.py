import re

from django.db import migrations


def fix_section_durations(apps, schema_editor):
    Section = apps.get_model("assessment", "Section")
    for section in Section.objects.all():
        new_title = re.sub(r'\(45\s+MINUTES?\)', "(60 MINUTES)", section.title, flags=re.IGNORECASE)
        if new_title != section.title:
            section.title = new_title
            section.save(update_fields=["title"])


class Migration(migrations.Migration):
    dependencies = [("assessment", "0057_attempt_timed_out")]

    operations = [
        migrations.RunPython(fix_section_durations, migrations.RunPython.noop),
    ]
