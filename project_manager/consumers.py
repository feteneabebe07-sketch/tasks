# pm/consumers.py
import json
import redis
import os
import logging
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async
from django.contrib.auth.models import User
from django.utils import timezone

# Logger for consumer debugging
logger = logging.getLogger(__name__)

# Redis connection (use REDIS_URL env var in production)
_redis_url = os.environ.get('REDIS_URL', 'redis://127.0.0.1:6379/0')
redis_client = redis.from_url(_redis_url)

class MessageConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        # Log incoming connection scope for debugging
        try:
            logger.info('WebSocket connect scope: %s', {k: v for k, v in self.scope.items() if k != 'headers'})
        except Exception:
            logger.exception('Failed to log scope for WS connect')

        self.user = self.scope.get('user')

        if not getattr(self.user, 'is_authenticated', False):
            logger.info('WebSocket connection rejected: anonymous user')
            await self.close()
            return
        
        self.user_id = str(self.user.id)
        
        # Set user as online
        redis_client.setex(f'user_online_{self.user_id}', 300, '1')  # 5 minutes
        
        # Store last seen
        redis_client.set(f'user_last_seen_{self.user_id}', timezone.now().isoformat())
        
        # Accept connection
        await self.accept()
        logger.info('WebSocket accepted connection for user=%s', self.user_id)
        
        # Send ping to keep connection alive
        await self.send(text_data=json.dumps({
            'type': 'ping',
            'message': 'Connected successfully'
        }))
        
        # Notify others that user is online
        await self.broadcast_user_status(True)
    
    async def disconnect(self, close_code):
        logger.info('WebSocket disconnect: user=%s close_code=%s', getattr(self, 'user_id', None), close_code)
        # Remove from online users
        try:
            redis_client.delete(f'user_online_{self.user_id}')
        except Exception:
            logger.exception('Failed to delete redis online key for user %s', getattr(self, 'user_id', None))
        
        # Notify others that user is offline
        try:
            await self.broadcast_user_status(False)
        except Exception:
            logger.exception('Failed to broadcast user offline status for %s', getattr(self, 'user_id', None))
    
    async def receive(self, text_data):
        try:
            logger.info('WebSocket received from user=%s data=%s', getattr(self, 'user_id', None), text_data)
        except Exception:
            logger.exception('Failed to log incoming WS message')

        try:
            data = json.loads(text_data)
        except Exception:
            logger.exception('Invalid JSON received via WebSocket: %s', text_data)
            return
        message_type = data.get('type')
        
        logger.info('Handling WS message type=%s for user=%s', message_type, getattr(self, 'user_id', None))
        if message_type == 'authenticate':
            # User already authenticated via Django session
            await self.send(text_data=json.dumps({
                'type': 'authenticated',
                'user_id': self.user_id,
                'user_name': self.user.get_full_name()
            }))
        
        elif message_type == 'direct_message':
            await self.handle_direct_message(data)
        
        elif message_type == 'typing':
            await self.handle_typing(data)
        
        elif message_type == 'message_read':
            await self.handle_message_read(data)
        
        elif message_type == 'pong':
            # Update online status
            redis_client.setex(f'user_online_{self.user_id}', 300, '1')
    
    async def handle_direct_message(self, data):
        recipient_id = data.get('recipient_id')
        content = data.get('content')
        
        # Save message to database
        message = await self.save_message(recipient_id, content)
        
        # Publish to Redis for real-time delivery
        message_data = {
            'type': 'direct_message',
            'message_id': message.id,
            'sender_id': self.user_id,
            'sender_name': self.user.get_full_name(),
            'recipient_id': recipient_id,
            'content': content,
            'timestamp': message.created_at.isoformat(),
            'avatar_color': self.get_user_color(self.user_id),
            'initials': f"{self.user.first_name[0]}{self.user.last_name[0]}" if self.user.first_name and self.user.last_name else self.user.username[:2].upper(),
        }
        
        # Publish to recipient's Redis channel
        redis_client.publish(f'user_{recipient_id}', json.dumps(message_data))
        
        # Also send to sender for UI update
        await self.send(text_data=json.dumps(message_data))
    
    async def handle_typing(self, data):
        recipient_id = data.get('recipient_id')
        is_typing = data.get('is_typing')
        
        # Send typing indicator to recipient
        typing_data = {
            'type': 'typing',
            'sender_id': self.user_id,
            'sender_name': self.user.get_full_name(),
            'recipient_id': recipient_id,
            'is_typing': is_typing
        }
        
        redis_client.publish(f'user_{recipient_id}', json.dumps(typing_data))
    
    async def handle_message_read(self, data):
        message_id = data.get('message_id')
        recipient_id = data.get('recipient_id')
        
        # Update message read status in database
        await self.mark_message_as_read(message_id)
        
        # Send read receipt to sender
        read_data = {
            'type': 'message_read',
            'message_id': message_id,
            'recipient_id': recipient_id
        }
        
        redis_client.publish(f'user_{recipient_id}', json.dumps(read_data))
    
    async def broadcast_user_status(self, is_online):
        """Broadcast user status to all connected clients"""
        # In a real app, you would get all users who have conversations with this user
        # For simplicity, we'll publish to a general channel
        status_data = {
            'type': 'user_status',
            'user_id': self.user_id,
            'user_name': self.user.get_full_name(),
            'is_online': is_online,
            'last_seen': timezone.now().isoformat() if not is_online else None
        }
        
        # Publish to general channel
        redis_client.publish('user_status_updates', json.dumps(status_data))
    
    @database_sync_to_async
    def save_message(self, recipient_id, content):
        from core.models import Message, User
        recipient = User.objects.get(id=recipient_id)
        
        message = Message.objects.create(
            sender=self.user,
            message_type='direct',
            content=content,
            created_at=timezone.now()
        )
        message.recipients.add(recipient)
        
        return message
    
    @database_sync_to_async
    def mark_message_as_read(self, message_id):
        from core.models import Message
        try:
            message = Message.objects.get(id=message_id)
            message.is_read = True
            message.save()
        except Message.DoesNotExist:
            pass
    
    def get_user_color(self, user_id):
        colors = ['dark-teal', 'dark-cyan', 'golden-orange', 'rusty-spice', 'oxidized-iron', 'brown-red']
        user_id_int = int(user_id)
        return f"bg-{colors[user_id_int % len(colors)]}"