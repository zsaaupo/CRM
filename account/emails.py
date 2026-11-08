import logging
from threading import Thread

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.utils.html import escape


logger = logging.getLogger(__name__)


def send_mail(to, subject, body):
    """Send plain-text and escaped HTML content through Django's mail backend."""
    message = EmailMultiAlternatives(
        subject=subject,
        body=body,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[to],
    )
    normalized_body = body.replace('\r\n', '\n').replace('\r', '\n')
    html_body = '<html><body>{}</body></html>'.format(
        escape(normalized_body).replace('\n', '<br>\n'),
    )
    message.attach_alternative(html_body, 'text/html')
    return message.send(fail_silently=False)


def _send_mail_in_background(to, subject, body):
    try:
        if send_mail(to, subject, body) != 1:
            logger.error('The mail backend did not accept the signup verification email.')
    except Exception:
        # Thread failures cannot be returned to the completed signup request.
        logger.exception('Could not send the signup verification email.')


def thread_send_email(to, subject, body):
    """Start a best-effort email thread; call this only after a database commit."""
    try:
        Thread(
            target=_send_mail_in_background,
            args=(to, subject, body),
            name='signup-email',
            daemon=True,
        ).start()
    except RuntimeError:
        logger.exception('Could not start the signup email thread.')
