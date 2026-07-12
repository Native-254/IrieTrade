# monitoring/email_alerter.py
import smtplib
import os
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from utils.logger import log

class EmailAlerter:
    def __init__(self):
        self.sender = os.getenv('EMAIL_SENDER')
        self.password = os.getenv('EMAIL_PASSWORD')
        self.recipient = os.getenv('EMAIL_RECIPIENT')
        self.enabled = all([self.sender, self.password, self.recipient])
        if self.enabled:
            log.info("Email alerter initialized.")

    def send_message(self, subject: str, body: str):
        if not self.enabled:
            return

        # At this point we know all three are set, tell Pylance that
        assert self.sender is not None
        assert self.password is not None
        assert self.recipient is not None

        msg = MIMEMultipart()
        msg['From'] = self.sender
        msg['To'] = self.recipient
        msg['Subject'] = subject
        msg.attach(MIMEText(body, 'plain'))

        try:
            server = smtplib.SMTP('smtp.gmail.com', 587)
            server.starttls()
            server.login(self.sender, self.password)
            server.send_message(msg)
            server.quit()
            log.debug(f"Email sent: {subject}")
        except Exception as e:
            log.error(f"Failed to send email: {e}")

    def send_trade_alert(self, symbol, action, quantity, price):
        subj = f"IrieTrade: {action} {quantity} {symbol} @ ${price:.2f}"
        body = f"Symbol: {symbol}\nAction: {action}\nQty: {quantity}\nPrice: {price:.2f}"
        self.send_message(subj, body)

    def send_error_alert(self, error_msg):
        self.send_message("IrieTrade Error", error_msg)