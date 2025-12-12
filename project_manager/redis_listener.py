# pm/redis_listener.py
import json
import redis
from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync

redis_client = redis.Redis(host='172.21.28.17', port=6379, db=0)
channel_layer = get_channel_layer()

def listen_for_messages():
    """Listen for Redis messages and forward to WebSocket"""
    pubsub = redis_client.pubsub()
    
    # Subscribe to user channels and status updates
    pubsub.subscribe('user_status_updates')
    
    print("Redis listener started...")
    
    for message in pubsub.listen():
        if message['type'] == 'message':
            data = json.loads(message['data'])
            
            if 'recipient_id' in data:
                # Forward to user's WebSocket
                async_to_sync(channel_layer.group_send)(
                    f'user_{data["recipient_id"]}',
                    {
                        'type': 'send_message',
                        'message': data
                    }
                )
            elif data.get('type') == 'user_status':
                # Broadcast user status to all
                async_to_sync(channel_layer.group_send)(
                    'user_status',
                    {
                        'type': 'user_status_update',
                        'message': data
                    }
                )