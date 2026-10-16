import logging
import math

from django.core.exceptions import ObjectDoesNotExist
from django.http import Http404
from rest_framework import status
from rest_framework.exceptions import Throttled
from rest_framework.response import Response
from rest_framework.views import exception_handler

logger = logging.getLogger(__name__)


def custom_exception_handler(exc, context):
    if isinstance(exc, ObjectDoesNotExist):
        exc = Http404()

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
        if isinstance(exc, Throttled) and exc.wait is not None:
            # Body copy of Retry-After so the FE needn't read the header.
            retry_after = math.ceil(exc.wait)
            response.data["retry_after"] = retry_after
            response["Retry-After"] = str(retry_after)
        return response

    logger.exception("Unhandled exception in API view")
    return Response(
        {"detail": "Internal server error.", "status": "error"},
        status=status.HTTP_500_INTERNAL_SERVER_ERROR,
    )
