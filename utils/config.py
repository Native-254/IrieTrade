# utils/config.py
import os
import yaml
from dotenv import load_dotenv

load_dotenv()

def load_config(config_path='config/settings.yaml'):
    """Loads the YAML configuration file."""
    with open(config_path, 'r') as f:
        config = yaml.safe_load(f)

    # Override sensitive values from environment variables
    config['exchanges']['ib']['account_id'] = os.getenv('IB_ACCOUNT_ID', config['exchanges']['ib']['account_id'])
    config['monitoring']['telegram']['bot_token'] = os.getenv('TELEGRAM_BOT_TOKEN')
    config['monitoring']['telegram']['chat_id'] = os.getenv('TELEGRAM_CHAT_ID')
    config['monitoring']['discord']['webhook_url'] = os.getenv('DISCORD_WEBHOOK_URL', config['monitoring']['discord']['webhook_url'])
    return config

CONFIG = load_config()