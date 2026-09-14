"""V1 keeps only durable execution fences and upload evidence; no mutable progress framework."""
def record_progress(*_args,**_kwargs):
    return None
