from __future__ import annotations


class EventsService:
    def __init__(self, repository):
        self.repository = repository

    def list(self, query):
        events, controls, last_key = self.repository.page(query)
        items = []
        for event in events:
            control = controls.get((f"EVENT#{event['event_id']}", "CONTROL"), {})
            status = control.get("notification_status", "UNKNOWN")
            public = {key: value for key, value in event.items() if key not in {"PK", "SK"}}
            public["notification"] = {
                "status": status,
                "published_at": control.get("published_at"),
                "email_recipient": control.get("email_recipient"),
                "email_status": control.get("email_status", "NOT_TRACKED"),
                "email_recipients": control.get("email_recipients", []),
                "suppression_reason": control.get("notification_suppression_reason"),
                "channels": control.get("publications", {}),
                "push": {"status": control.get("push_status", "NOT_CONFIGURED"),
                         "sns_message_id": control.get("push_message_id"),
                         "published_at": control.get("push_published_at")},
                # Provider acceptance is observable; inbox delivery is not.
                "delivery_confirmed": None,
            }
            items.append(public)
        return {"items": items, "next_cursor": query.cursor(last_key), "filters": query.filters()}
