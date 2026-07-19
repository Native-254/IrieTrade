# test_discord.py
from monitoring.discord_alerter import DiscordAlerter

alerter = DiscordAlerter()
alerter.send_message("✅ Discord integration test successful!")
alerter.send_trade_alert("AAPL", "BUY", 100, 150.25)