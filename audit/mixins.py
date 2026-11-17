from .services import form_changes, log_audit


class AuditedAdminMixin:
    def save_model(self, request, obj, form, change):
        changes = form_changes(form) if change else {}
        super().save_model(request, obj, form, change)
        log_audit(request, "updated (admin)" if change else "created (admin)", obj, changes=changes)

    def delete_model(self, request, obj):
        info = (obj.__class__.__name__, str(obj.pk), str(obj))
        super().delete_model(request, obj)
        log_audit(request, "deleted (admin)", entity_type=info[0], entity_id=info[1], entity_label=info[2])

    def delete_queryset(self, request, queryset):
        items = [(o.__class__.__name__, str(o.pk), str(o)) for o in queryset]
        super().delete_queryset(request, queryset)
        for entity_type, entity_id, label in items:
            log_audit(request, "deleted (admin)", entity_type=entity_type,
                      entity_id=entity_id, entity_label=label)