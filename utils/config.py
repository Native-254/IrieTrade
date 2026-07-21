# utils/config.py 
import os
import yaml
from dotenv import load_dotenv
from utils.security import decrypt

load_dotenv()

def _env(key: str, default: str = '') -> str:
    """Return decrypted environment variable."""
    return decrypt(os.getenv(key, default))

def load_config(config_path='config/settings.yaml'):
    with open(config_path, 'r') as f:
        config = yaml.safe_load(f)

    config['exchanges']['ib']['account_id'] = _env('IB_ACCOUNT_ID', config['exchanges']['ib']['account_id'])
    config['monitoring']['telegram']['bot_token'] = _env('TELEGRAM_BOT_TOKEN')
    config['monitoring']['telegram']['chat_id'] = _env('TELEGRAM_CHAT_ID')
    config['monitoring']['discord']['webhook_url'] = _env('DISCORD_WEBHOOK_URL', config['monitoring']['discord']['webhook_url'])
    return config

CONFIG = load_config()