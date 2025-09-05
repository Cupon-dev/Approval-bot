import os
import logging
import json
from telegram import Update
from telegram.ext import Updater, CommandHandler, CallbackContext, ChatMemberHandler
from datetime import datetime
import time

# Enable logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO
)
logger = logging.getLogger(__name__)

# Get bot token from environment variable
TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')

# Get channel usernames from environment variable (comma-separated)
CHANNEL_USERNAMES = os.getenv('CHANNEL_USERNAMES', '').split(',')
CHANNEL_USERNAMES = [username.strip().replace('@', '') for username in CHANNEL_USERNAMES if username.strip()]

# Suspicious criteria
MIN_ACCOUNT_AGE_DAYS = 30
SUSPICIOUS_NAMES = ["deleted account", "bot", "bots", "police", "telegram", "admin", "support", 
                    "official", "http", "www", ".com", ".ru", ".xyz", "click", "promo", "sales"]

# File to store left users
LEFT_USERS_FILE = "left_users.json"

def load_left_users():
    if os.path.exists(LEFT_USERS_FILE):
        try:
            with open(LEFT_USERS_FILE, 'r') as f:
                return json.load(f)
        except:
            return {}
    return {}

def save_left_users(data):
    with open(LEFT_USERS_FILE, 'w') as f:
        json.dump(data, f)

left_users = load_left_users()

def is_suspicious_user(user):
    if hasattr(user, 'created_at') and user.created_at:
        account_age_days = (datetime.now() - user.created_at).days
        if account_age_days < MIN_ACCOUNT_AGE_DAYS:
            return True, f"Account too new ({account_age_days} days)"
    
    if user.username:
        username_lower = user.username.lower()
        for keyword in SUSPICIOUS_NAMES:
            if keyword in username_lower:
                return True, f"Suspicious username: {user.username}"
    
    if user.first_name:
        first_name_lower = user.first_name.lower()
        for keyword in SUSPICIOUS_NAMES:
            if keyword in first_name_lower:
                return True, f"Suspicious first name: {user.first_name}"
    
    if user.last_name:
        last_name_lower = user.last_name.lower()
        for keyword in SUSPICIOUS_NAMES:
            if keyword in last_name_lower:
                return True, f"Suspicious last name: {user.last_name}"
    
    if not user.username:
        return True, "No username"
    
    return False, "User appears legitimate"

def track_chat_members(update: Update, context: CallbackContext):
    global left_users
    if update.chat_member:
        chat_username = update.effective_chat.username
        if chat_username not in CHANNEL_USERNAMES:
            return
            
        old_status = update.chat_member.old_chat_member.status
        new_status = update.chat_member.new_chat_member.status
        
        if (old_status in ['member', 'administrator', 'creator'] and 
            new_status in ['left', 'kicked']):
            user_id = str(update.chat_member.new_chat_member.user.id)
            
            if user_id not in left_users:
                left_users[user_id] = []
            
            if chat_username not in left_users[user_id]:
                left_users[user_id].append(chat_username)
                save_left_users(left_users)
            
            logger.info(f"User {user_id} left channel {chat_username}, added to manual approval list")

def approve_all_pending(context: CallbackContext):
    global left_users
    try:
        job_data = context.job.context
        chat_id = job_data.get('chat_id')
        specific_channel = job_data.get('channel')
        
        channels_to_process = [specific_channel] if specific_channel else CHANNEL_USERNAMES
        
        total_approved = 0
        total_rejected = 0
        results = []
        
        for channel_username in channels_to_process:
            if not channel_username:
                continue
                
            try:
                result = context.bot.get_chat_join_requests(channel_username)
                pending_requests = [] if not result else result
                
                approved_count = 0
                rejected_count = 0
                
                for request in pending_requests:
                    user = request.from_user
                    
                    if str(user.id) in left_users and channel_username in left_users[str(user.id)]:
                        context.bot.decline_chat_join_request(channel_username, user.id)
                        logger.info(f"Declined user {user.username or user.first_name} (previously left {channel_username})")
                        rejected_count += 1
                        continue
                    
                    is_suspicious, reason = is_suspicious_user(user)
                    
                    if not is_suspicious:
                        context.bot.approve_chat_join_request(channel_username, user.id)
                        logger.info(f"Approved user: {user.username or user.first_name} for {channel_username}")
                        approved_count += 1
                    else:
                        context.bot.decline_chat_join_request(channel_username, user.id)
                        logger.warning(f"Declined suspicious user: {user.username or user.first_name} for {channel_username} - Reason: {reason}")
                        rejected_count += 1
                
                results.append(f"{channel_username}: Approved {approved_count}, Rejected {rejected_count}")
                total_approved += approved_count
                total_rejected += rejected_count
                
                time.sleep(1)
                
            except Exception as e:
                error_msg = f"Error processing {channel_username}: {str(e)}"
                logger.error(error_msg)
                results.append(error_msg)
        
        if chat_id:
            summary = f"Approval process completed!\nTotal Approved: {total_approved}\nTotal Rejected: {total_rejected}\n\n" + "\n".join(results)
            context.bot.send_message(chat_id, summary)
                
    except Exception as e:
        logger.error(f"Error in approve_all_pending: {e}")
        if context.job and context.job.context:
            chat_id = context.job.context.get('chat_id')
            if chat_id:
                context.bot.send_message(chat_id, f"Error processing join requests: {e}")

def start_approval(update: Update, context: CallbackContext):
    if update.effective_chat.type in ['group', 'supergroup', 'channel']:
        try:
            member = context.bot.get_chat_member(update.effective_chat.id, update.effective_user.id)
            if member.status not in ['administrator', 'creator']:
                update.message.reply_text("You need to be an admin to use this command.")
                return
        except:
            update.message.reply_text("Error verifying admin status.")
            return
        
        specific_channel = None
        if context.args:
            channel_arg = context.args[0].replace('@', '')
            if channel_arg in CHANNEL_USERNAMES:
                specific_channel = channel_arg
                update.message.reply_text(f"Starting approval process for @{specific_channel}...")
            else:
                update.message.reply_text(f"Channel @{channel_arg} not found in monitored channels.")
                return
        else:
            update.message.reply_text("Starting approval process for all channels...")
        
        context.job_queue.run_once(
            approve_all_pending, 
            when=1, 
            context={'chat_id': update.effective_chat.id, 'channel': specific_channel}
        )
    else:
        update.message.reply_text("This command can only be used in group/channel chats.")

def manual_approve(update: Update, context: CallbackContext):
    global left_users
    if update.effective_chat.type in ['group', 'supergroup', 'channel']:
        try:
            member = context.bot.get_chat_member(update.effective_chat.id, update.effective_user.id)
            if member.status not in ['administrator', 'creator']:
                update.message.reply_text("You need to be an admin to use this command.")
                return
        except:
            update.message.reply_text("Error verifying admin status.")
            return
        
        if not context.args or len(context.args) < 1:
            update.message.reply_text("Please provide a user ID to approve. Usage: /approve_user <user_id> [channel]")
            return
        
        try:
            user_id = str(context.args[0])
            channel_username = context.args[1].replace('@', '') if len(context.args) > 1 else None
            
            channels_to_approve = [channel_username] if channel_username else CHANNEL_USERNAMES
            
            approved_channels = []
            for channel in channels_to_approve:
                if channel not in CHANNEL_USERNAMES:
                    continue
                
                if user_id in left_users and channel in left_users[user_id]:
                    left_users[user_id].remove(channel)
                    if not left_users[user_id]:
                        del left_users[user_id]
                    save_left_users(left_users)
                
                context.bot.approve_chat_join_request(channel, int(user_id))
                logger.info(f"Manually approved user {user_id} for {channel}")
                approved_channels.append(channel)
            
            if approved_channels:
                channel_list = ", ".join([f"@{c}" for c in approved_channels])
                update.message.reply_text(f"User {user_id} has been manually approved for {channel_list}.")
            else:
                update.message.reply_text(f"No channels were approved for user {user_id}.")
            
        except ValueError:
            update.message.reply_text("Please provide a valid numeric user ID.")
        except Exception as e:
            update.message.reply_text(f"Error approving user: {e}")
    else:
        update.message.reply_text("This command can only be used in group/channel chats.")

def list_left_users(update: Update, context: CallbackContext):
    global left_users
    if update.effective_chat.type in ['group', 'supergroup', 'channel']:
        try:
            member = context.bot.get_chat_member(update.effective_chat.id, update.effective_user.id)
            if member.status not in ['administrator', 'creator']:
                update.message.reply_text("You need to be an admin to use this command.")
                return
        except:
            update.message.reply_text("Error verifying admin status.")
            return
        
        if not left_users:
            update.message.reply_text("No users in the manual approval list.")
        else:
            message = "Users requiring manual approval:\n\n"
            for user_id, channels in left_users.items():
                message += f"User ID: {user_id}\nChannels: {', '.join([f'@{c}' for c in channels])}\n\n"
            
            if len(message) > 4096:
                parts = [message[i:i+4096] for i in range(0, len(message), 4096)]
                for part in parts:
                    update.message.reply_text(part)
            else:
                update.message.reply_text(message)
    else:
        update.message.reply_text("This command can only be used in group/channel chats.")

def list_channels(update: Update, context: CallbackContext):
    if not CHANNEL_USERNAMES:
        update.message.reply_text("No channels are being monitored.")
    else:
        channels_list = "\n".join([f"• @{channel}" for channel in CHANNEL_USERNAMES])
        update.message.reply_text(f"Channels monitored by this bot:\n{channels_list}")

def start(update: Update, context: CallbackContext):
    help_text = """
Hi! I am a multi-channel approval bot.

Commands:
/approve_all [channel] - Process all pending join requests
/approve_user <user_id> [channel] - Manually approve a user
/list_left_users - List users requiring manual approval
/list_channels - List all monitored channels
/help - Show this help message
"""
    update.message.reply_text(help_text)

def help_command(update: Update, context: CallbackContext):
    start(update, context)

def error(update: Update, context: CallbackContext):
    logger.warning('Update "%s" caused error "%s"', update, context.error)

def main():
    if not TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN environment variable is required!")
        return
    
    if not CHANNEL_USERNAMES:
        logger.error("CHANNEL_USERNAMES environment variable is required!")
        return
    
    logger.info(f"Monitoring channels: {', '.join(CHANNEL_USERNAMES)}")
    
    updater = Updater(TOKEN)
    dispatcher = updater.dispatcher

    dispatcher.add_handler(CommandHandler("start", start))
    dispatcher.add_handler(CommandHandler("help", help_command))
    dispatcher.add_handler(CommandHandler("approve_all", start_approval))
    dispatcher.add_handler(CommandHandler("approve_user", manual_approve))
    dispatcher.add_handler(CommandHandler("list_left_users", list_left_users))
    dispatcher.add_handler(CommandHandler("list_channels", list_channels))
    
    dispatcher.add_handler(ChatMemberHandler(track_chat_members))
    dispatcher.add_error_handler(error)

    updater.start_polling()
    logger.info("Bot started and polling for updates...")
    updater.idle()

if __name__ == '__main__':
    main()
