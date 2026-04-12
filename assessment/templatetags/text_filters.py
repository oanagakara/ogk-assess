import re
from django import template
from django.utils.html import escape
from django.utils.safestring import mark_safe

register = template.Library()


@register.filter
def flow_paragraphs(value):
    """
    Render passage text naturally:
    - Normalises Windows (\\r\\n) line endings
    - Splits on blank lines (double newlines) into paragraphs
    - Within each paragraph, collapses soft-wrap newlines into spaces
    - Further splits each paragraph at sentence boundaries so sentences
      each get their own <p> tag
    """
    if not value:
        return value

    # Normalise Windows line endings
    text = value.replace("\r\n", "\n").replace("\r", "\n")

    parts = []
    for block in re.split(r"\n{2,}", text):
        block = block.replace("\n", " ").strip()
        if block:
            parts.append(f"<p>{escape(block)}</p>")

    return mark_safe("".join(parts))
