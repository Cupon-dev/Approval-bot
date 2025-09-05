import os
import logging
import json
import sys

# Check for required dependencies first
try:
    from telegram import Update
    from telegram.ext import Updater, CommandHandler, CallbackContext, ChatMemberHandler
    from datetime import datetime
    import time
    import urllib3
except ImportError as e:
    print(f"❌ Missing dependency: {e}")
    print("Please make sure requirements.txt includes:")
    print("- python-telegram-bot==13.7")
    print("- python-dotenv==0.19.0") 
    print("- urllib3==1.26.18")
    sys.exit(1)

# Enable logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', 
    level=logging.INFO,
    stream=sys.stdout
)
logger = logging.getLogger(__name__)

# Get bot token from environment variable
TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')

# Get channel IDs from environment variable (comma-separated)
CHANNEL_IDS = os.getenv('CHANNEL_IDS', '')
if CHANNEL_IDS:
    CHANNEL_IDS = [int(channel_id.strip()) for channel_id in CHANNEL_IDS.split(',') if channel_id.strip()]
else:
    CHANNEL_IDS = []

# Store channel info (ID -> username mapping)
channel_info = {}

# Suspicious criteria
MIN_ACCOUNT_AGE_DAYS = 30
SUSPICIOUS_NAMES = ["deleted account", "bot", "bots", "police", "telegram", "admin", "support", 
                    "official", "http", "www", ".com", ".ru", ".xyz", "click", "promo", "sales"]

# File to store left users
LEFT_USERS_FILE = "left_users.json"

def check_environment():
    """Check if all required environment variables are set"""
    required_vars = ['TELEGRAM_BOT_TOKEN', 'CHANNEL_IDS']
    missing_vars = []
    
    for var in required_vars:
        if not os.getenv(var):
            missing_vars.append(var)
    
    if missing_vars:
        error_msg = f"Missing required environment variables: {', '.join(missing_vars)}"
        logger.error(error_msg)
        return False
    
    if not CHANNEL_IDS:
        logger.error("CHANNEL_IDS is empty or not properly formatted")
        return False
    
    logger.info(f"✅ Environment variables check passed")
    logger.info(f"📊 Monitoring {len(CHANNEL_IDS)} channels")
    return True

def load_left_users():
    if os.path.exists(LEFT_USERS_FILE):
        try:
            with open(LEFT_USERS_FILE, 'r') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Error loading left users: {e}")
            return {}
    return {}

def save_left_users(data):
    try:
        with open(LEFT_USERS_FILE, 'w') as f:
            json.dump(data, f)
    except Exception as e:
        logger.error(f"Error saving left users: {e}")

left_users = load_left_users()

def get_channel_username(context, channel_id):
    """Get channel username from ID, with caching"""
    global channel_info
    
    if channel_id in channel_info:
        return channel_info[channel_id]
    
    try:
        chat = context.bot.get_chat(channel_id)
        channel_info[channel_id] = chat.username or f"ID_{channel_id}"
        return channel_info[channel_id]
    except Exception as e:
        logger.error(f"Error getting channel info for {channel_id}: {e}")
        return f"ID_{channel_id}"

def is_suspicious_user(user):
    try:
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
    except Exception as e:
        logger.error(f"Error in is_suspicious_user: {e}")
        return True, f"Error checking user: {e}"

def track_chat_members(update: Update, context: CallbackContext):
    global left_users
    try:
        if update.chat_member:
            chat_id = update.effective_chat.id
            if chat_id not in CHANNEL_IDS:
                return
                
            old_status = update.chat_member.old_chat_member.status
            new_status = update.chat_member.new_chat_member.status
            
            if (old_status in ['member', 'administrator', 'creator'] and 
                new_status in ['left', 'kicked']):
                user_id = str(update.chat_member.new_chat_member.user.id)
                
                if user_id not in left_users:
                    left_users[user_id] = []
                
                if chat_id not in left_users[user_id]:
                    left_users[user_id].append(chat_id)
                    save_left_users(left_users)
                
                channel_name = get_channel_username(context, chat_id)
                logger.info(f"User {user_id} left channel {channel_name}, added to manual approval list")
    except Exception as e:
        logger.error(f"Error in track_chat_members: {e}")

def approve_all_pending(context: CallbackContext):
    global left_users
    try:
        job_data = context.job.context
        chat_id = job_data.get('chat_id')
        specific_channel_id = job_data.get('channel_id')
        
        channels_to_process = [specific_channel_id] if specific_channel_id else CHANNEL_IDS
        
        total_approved = 0
        total_rejected = 0
        results = []
        
        for channel_id in channels_to_process:
            if not channel_id:
                continue
                
            try:
                # Get pending join requests for this channel
                result = context.bot.get_chat_join_requests(channel_id)
                pending_requests = [] if not result else result
                
                approved_count = 0
                rejected_count = 0
                
                for request in pending_requests:
                    user = request.from_user
                    
                    # Check if user previously left this channel
                    if str(user.id) in left_users and channel_id in left_users[str(user.id)]:
                        context.bot.decline_chat_join_request(channel_id, user.id)
                        channel_name = get_channel_username(context, channel_id)
                        logger.info(f"Declined user {user.username or user.first_name} (previously left {channel_name})")
                        rejected_count += 1
                        continue
                    
                    # Check if user is suspicious
                    is_suspicious, reason = is_suspicious_user(user)
                    
                    if not is_suspicious:
                        # Approve the request
                        context.bot.approve_chat_join_request(channel_id, user.id)
                        channel_name = get_channel_username(context, channel_id)
                        logger.info(f"Approved user: {user.username or user.first_name} for {channel_name}")
                        approved_count += 1
                    else:
                        # Decline the request if suspicious
                        context.bot.decline_chat_join_request(channel_id, user.id)
                        channel_name = get_channel_username(context, channel_id)
                        logger.warning(f"Declined suspicious user: {user.username or user.first_name} for {channel_name} - Reason: {reason}")
                        rejected_count += 1
                
                channel_name = get_channel_username(context, channel_id)
                results.append(f"{channel_name}: Approved {approved_count}, Rejected {rejected_count}")
                total_approved += approved_count
                total_rejected += rejected_count
                
                # Add delay between channels to avoid rate limiting
                time.sleep(1)
                
            except Exception as e:
                channel_name = get_channel_username(context, channel_id)
                error_msg = f"Error processing {channel_name}: {str(e)}"
                logger.error(error_msg)
                results.append(error_msg)
        
        # Send report if in a chat
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
        
        # Check if a specific channel was mentioned
        specific_channel_id = None
        if context.args:
            # Try to parse as channel ID first
            try:
                channel_arg = int(context.args[0])
                if channel_arg in CHANNEL_IDS:
                    specific_channel_id = channel_arg
                    channel_name = get_channel_username(context, specific_channel_id)
                    update.message.reply_text(f"Starting approval process for {channel_name}...")
                else:
                    update.message.reply_text(f"Channel ID {channel_arg} not found in monitored channels.")
                    return
            except ValueError:
                update.message.reply_text("Please provide a valid channel ID (numeric).")
                return
        else:
            update.message.reply_text("Starting approval process for all channels...")
        
        # Run approval process with job context to send report
        context.job_queue.run_once(
            approve_all_pending, 
            when=1, 
            context={'chat_id': update.effective_chat.id, 'channel_id': specific_channel_id}
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
        
        # Check if user ID was provided
        if not context.args or len(context.args) < 1:
            update.message.reply_text("Please provide a user ID to approve. Usage: /approve_user <user_id> [channel_id]")
            return
        
        try:
            user_id = str(context.args[0])
            channel_id = int(context.args[1]) if len(context.args) > 1 else None
            
            # If no channel specified, approve for all channels
            channels_to_approve = [channel_id] if channel_id else CHANNEL_IDS
            
            approved_channels = []
            for channel in channels_to_approve:
                if channel not in CHANNEL_IDS:
                    continue
                
                # Remove from left users list for this channel
                if user_id in left_users and channel in left_users[user_id]:
                    left_users[user_id].remove(channel)
                    if not left_users[user_id]:
                        del left_users[user_id]
                    save_left_users(left_users)
                
                # Approve the user for this channel
                context.bot.approve_chat_join_request(channel, int(user_id))
                channel_name = get_channel_username(context, channel)
                logger.info(f"Manually approved user {user_id} for {channel_name}")
                approved_channels.append(channel_name)
            
            if approved_channels:
                channel_list = ", ".join([f"{c}" for c in approved_channels])
                update.message.reply_text(f"User {user_id} has been manually approved for {channel_list}.")
            else:
                update.message.reply_text(f"No channels were approved for user {user_id}.")
            
        except ValueError:
            update.message.reply_text("Please provide valid numeric IDs.")
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
            for user_id, channel_ids in left_users.items():
                channel_names = [get_channel_username(context, channel_id) for channel_id in channel_ids]
                message += f"User ID: {user_id}\nChannels: {', '.join(channel_names)}\n\n"
            
            # Telegram has a message length limit, so we might need to split
            if len(message) > 4096:
                parts = [message[i:i+4096] for i in range(0, len(message), 4096)]
                for part in parts:
                    update.message.reply_text(part)
            else:
                update.message.reply_text(message)
    else:
        update.message.reply_text("This command can only be used in group/channel chats.")

def list_channels(update: Update, context: CallbackContext):
    if not CHANNEL_IDS:
        update.message.reply_text("No channels are being monitored.")
    else:
        message = "Channels monitored by this bot:\n\n"
        for channel_id in CHANNEL_IDS:
            channel_name = get_channel_username(context, channel_id)
            message += f"• {channel_name} (ID: {channel_id})\n"
        
        update.message.reply_text(message)

def start(update: Update, context: CallbackContext):
    help_text = """
Hi! I am a multi-channel approval bot.

Commands:
/approve_all [channel_id] - Process all pending join requests
/approve_user <user_id> [channel_id] - Manually approve a user
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
    """Start the bot."""
    logger.info("🤖 Starting Telegram Approval Bot...")
    
    # Check environment first
    if not check_environment():
        logger.error("Bot cannot start due to missing environment variables")
        return
    
    try:
        # Create the Updater and pass it your bot's token.
        updater = Updater(TOKEN)

        # Get the dispatcher to register handlers
        dispatcher = updater.dispatcher

        # Register command handlers
        dispatcher.add_handler(CommandHandler("start", start))
        dispatcher.add_handler(CommandHandler("help", help_command))
        dispatcher.add_handler(CommandHandler("approve_all", start_approval))
        dispatcher.add_handler(CommandHandler("approve_user", manual_approve))
        dispatcher.add_handler(CommandHandler("list_left_users", list_left_users))
        dispatcher.add_handler(CommandHandler("list_channels", list_channels))
        
        # Track when users leave the channels
        dispatcher.add_handler(ChatMemberHandler(track_chat_members))
        
        # Log all errors
        dispatcher.add_error_handler(error)

        # Start the Bot
        updater.start_polling()
        logger.info("✅ Bot started successfully and polling for updates...")
        logger.info(f"📊 Monitoring {len(CHANNEL_IDS)} channels")
        
        # Run the bot until interrupted
        updater.idle()
        
    except Exception as e:
        logger.error(f"❌ Failed to start bot: {e}")
        logger.error("Please check your TELEGRAM_BOT_TOKEN environment variable")

if __name__ == '__main__':
    main()
