from rest_framework.views import exception_handler


def custom_exception_handler(exc, context):
    response = exception_handler(exc, context)
    if response is not None:
        detail = response.data
        if isinstance(detail, dict):
            first_error = next(iter(detail.values()))
            if isinstance(first_error, list):
                detail = first_error[0]
            else:
                detail = str(first_error)
        elif isinstance(detail, list):
            detail = detail[0] if detail else "Error."
        else:
            detail = str(detail)
        response.data = {"detail": detail, "status": "error"}
    return response
