from django import template

register = template.Library()


@register.filter
def tr(dictionary, key):
    return dictionary.get(key, key)


@register.filter
def localized(item, field):
    return getattr(item, field + "_ar", "") or getattr(item, field)
