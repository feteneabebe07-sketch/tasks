# pm/messages_api.py
import json
import redis
import os
from datetime import datetime
from django.http import JsonResponse
from django.views.decorators.http import require_GET, require_POST
from django.views.decorators.csrf import csrf_exempt
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.utils import timezone
from core.models import User, EmployeeProfile, Message,Project, ProjectMember

# Redis connection
_redis_url = os.environ.get('REDIS_URL', 'redis://127.0.0.1:6379/0')
redis_client = redis.from_url(_redis_url)

@login_required
@require_GET
def get_conversation_messages(request, user_id):
    """Get messages for a specific conversation"""
    try:
        other_user = User.objects.get(id=user_id)
        current_user = request.user
        
        # Get messages between current user and other user
        messages = Message.objects.filter(
            Q(sender=current_user, recipients=other_user, message_type='direct') |
            Q(sender=other_user, recipients=current_user, message_type='direct')
        ).order_by('created_at')
        
        messages_data = []
        for msg in messages:
            is_sent = msg.sender == current_user
            
            # Mark as read if recipient is current user
            if not is_sent and not msg.is_read:
                msg.is_read = True
                msg.save()
            
            messages_data.append({
                'id': msg.id,
                'content': msg.content,
                'sender_id': msg.sender.id,
                'sender_name': msg.sender.get_full_name(),
                'initials': f"{msg.sender.first_name[0]}{msg.sender.last_name[0]}" if msg.sender.first_name and msg.sender.last_name else msg.sender.username[:2].upper(),
                'avatar_color': get_user_color(msg.sender.id),
                'timestamp': msg.created_at.isoformat(),
                'is_sent': is_sent,
                'is_read': msg.is_read,
                'date': msg.created_at.strftime('%Y-%m-%d'),
            })
        
        # Get other user info
        employee = EmployeeProfile.objects.filter(user=other_user).first()
        is_online = redis_client.exists(f'user_online_{other_user.id}')
        
        return JsonResponse({
            'success': True,
            'messages': messages_data,
            'other_user': {
                'id': other_user.id,
                'name': other_user.get_full_name(),
                'initials': f"{other_user.first_name[0]}{other_user.last_name[0]}" if other_user.first_name and other_user.last_name else other_user.username[:2].upper(),
                'avatar_color': get_user_color(other_user.id),
                'job_position': employee.job_position if employee else 'Team Member',
                'department': employee.department.name if employee and employee.department else 'No Department',
                'is_online': is_online,
            }
        })
        
    except User.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'User not found'})

@login_required
@require_POST
def send_message_api(request):
    """Send a direct message"""
    try:
        data = json.loads(request.body)
        recipient_id = data.get('recipient_id')
        content = data.get('content')
        
        if not recipient_id or not content:
            return JsonResponse({'success': False, 'error': 'Missing required fields'})
        
        recipient = User.objects.get(id=recipient_id)
        
        # Create message
        message = Message.objects.create(
            sender=request.user,
            message_type='direct',
            content=content,
            created_at=timezone.now()
        )
        message.recipients.add(recipient)
        
        # Publish to Redis for real-time delivery
        publish_message(message, request.user, recipient)
        
        return JsonResponse({
            'success': True,
            'message_id': message.id,
            'timestamp': message.created_at.isoformat(),
        })
        
    except User.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Recipient not found'})
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)})

@login_required
@require_POST
def mark_as_read_api(request, user_id):
    """Mark all messages from a user as read"""
    try:
        other_user = User.objects.get(id=user_id)
        
        # Mark messages as read
        messages = Message.objects.filter(
            sender=other_user,
            recipients=request.user,
            is_read=False,
            message_type='direct'
        )
        
        for msg in messages:
            msg.is_read = True
            msg.save()
        
        return JsonResponse({'success': True})
        
    except User.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'User not found'})

@login_required
@require_GET
def get_unread_count_api(request):
    """Get total unread message count"""
    unread_count = Message.objects.filter(
        recipients=request.user,
        is_read=False,
        message_type='direct'
    ).count()
    
    return JsonResponse({
        'success': True,
        'unread_count': unread_count
    })

@login_required
@require_POST
def start_conversation_api(request):
    """Start a new conversation"""
    try:
        data = json.loads(request.body)
        recipient_id = data.get('recipient_id')
        
        recipient = User.objects.get(id=recipient_id)
        
        # Check if conversation already exists
        existing_messages = Message.objects.filter(
            Q(sender=request.user, recipients=recipient, message_type='direct') |
            Q(sender=recipient, recipients=request.user, message_type='direct')
        ).exists()
        
        # Get recipient info
        employee = EmployeeProfile.objects.filter(user=recipient).first()
        is_online = redis_client.exists(f'user_online_{recipient.id}')
        
        return JsonResponse({
            'success': True,
            'user_id': recipient.id,
            'name': recipient.get_full_name(),
            'initials': f"{recipient.first_name[0]}{recipient.last_name[0]}" if recipient.first_name and recipient.last_name else recipient.username[:2].upper(),
            'avatar_color': get_user_color(recipient.id),
            'job_position': employee.job_position if employee else 'Team Member',
            'is_online': is_online,
            'has_existing_conversation': existing_messages,
        })
        
    except User.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'User not found'})

@login_required
@require_GET
def search_users_api(request):
    """Search for users to message"""
    query = request.GET.get('q', '')
    
    if len(query) < 2:
        return JsonResponse({'success': True, 'results': []})
    
    # Search in users from managed projects
    current_user = request.user
    managed_projects = Project.objects.filter(project_manager=current_user)
    
    project_members = ProjectMember.objects.filter(
        project__in=managed_projects,
        is_active=True
    ).select_related('employee__user').distinct()
    
    results = []
    for member in project_members:
        user = member.employee.user
        if user.id == current_user.id:
            continue
            
        full_name = user.get_full_name().lower()
        email = user.email.lower()
        query_lower = query.lower()
        
        if query_lower in full_name or query_lower in email:
            is_online = redis_client.exists(f'user_online_{user.id}')
            
            results.append({
                'id': user.id,
                'name': user.get_full_name(),
                'email': user.email,
                'job_position': member.employee.job_position,
                'project': member.project.name,
                'is_online': is_online,
                'avatar_color': get_user_color(user.id),
            })
    
    return JsonResponse({
        'success': True,
        'results': results[:10]  # Limit to 10 results
    })

def publish_message(message, sender, recipient):
    """Publish message to Redis for real-time delivery"""
    message_data = {
        'type': 'direct_message',
        'message_id': message.id,
        'sender_id': sender.id,
        'sender_name': sender.get_full_name(),
        'recipient_id': recipient.id,
        'content': message.content,
        'timestamp': message.created_at.isoformat(),
        'avatar_color': get_user_color(sender.id),
        'initials': f"{sender.first_name[0]}{sender.last_name[0]}" if sender.first_name and sender.last_name else sender.username[:2].upper(),
    }
    
    # Publish to recipient's channel
    redis_client.publish(f'user_{recipient.id}', json.dumps(message_data))
    
    # Also publish to sender's channel (for UI updates)
    redis_client.publish(f'user_{sender.id}', json.dumps(message_data))

def get_user_color(user_id):
    """Get consistent color for user avatar"""
    colors = ['dark-teal', 'dark-cyan', 'golden-orange', 'rusty-spice', 'oxidized-iron', 'brown-red']
    return f"bg-{colors[user_id % len(colors)]}"