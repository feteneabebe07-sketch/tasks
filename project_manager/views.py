# views/pm_views.py
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required, user_passes_test as _django_user_passes_test
from django.conf import settings

# Wrap user_passes_test to default to project's LOGIN_URL when '/login/' literal is used
def user_passes_test(test_func, login_url=None, **kwargs):
    # If code used the older placeholder '/login/', replace with configured LOGIN_URL
    if login_url == '/login/' or login_url is None:
        login_url = settings.LOGIN_URL
    return _django_user_passes_test(test_func, login_url=login_url, **kwargs)
from django.conf import settings
from django.http import JsonResponse
from django.db.models import Count, Sum, Avg, Q
from django.utils import timezone
from datetime import timedelta
from core.models import (
    User, EmployeeProfile, Department, Project, 
    Task, Sprint, ProjectMember, Message, Comment, 
    TimeLog, Notification, StandupUpdate
)
from .pm_helpers import calculate_member_task_statuses
def get_user_websocket_url(request):
    """Get WebSocket URL for the current user"""
    if request.is_secure():
        ws_scheme = "wss://"
    else:
        ws_scheme = "ws://"
    
    return f"{ws_scheme}{request.get_host()}/ws/messages/"

def is_project_manager(user):
    return user.is_authenticated and (user.role == 'pm' or user.role == 'admin')

@login_required
@user_passes_test(is_project_manager, login_url='/login/')
def pm_dashboard(request):
    """PM Dashboard with real data""" 
    current_user = request.user
    today = timezone.now().date()
    
    # Get projects managed by this PM
    managed_projects = Project.objects.filter(
        project_manager=current_user
    ).select_related('department')
    
    # Get the main active project (or first project if none active)
    active_project = managed_projects.filter(status='active').first()
    if not active_project:
        active_project = managed_projects.first()
    
    context = {
        'user': current_user,
        'managed_projects': managed_projects,
        'active_project': active_project,
    }
    
    if active_project:
        available_sprints = Sprint.objects.filter(
        project=active_project,
        status__in=['planned', 'active']
    ).order_by('-start_date')
        all_projects = managed_projects
        available_employees = EmployeeProfile.objects.filter(
        status='active'
    ).exclude(
        id__in=ProjectMember.objects.filter(
            project=active_project,
            is_active=True
        ).values_list('employee_id', flat=True)
    ).select_related('user', 'department')[:10]
        
        # Project statistics
        total_tasks = Task.objects.filter(project=active_project).count()
        active_tasks = Task.objects.filter(
            project=active_project,
            status__in=['todo', 'in_progress', 'review']
        ).count()
        completed_tasks = Task.objects.filter(
            project=active_project,
            status='done'
        ).count()
        
        # Calculate project progress
        if total_tasks > 0:
            project_progress = int((completed_tasks / total_tasks) * 100)
        else:
            project_progress = 0
        
        # Get active sprint
        active_sprint = Sprint.objects.filter(
            project=active_project,
            status='active'
        ).first()
        
        # Get team members for active project
        project_members = ProjectMember.objects.filter(
            project=active_project,
            is_active=True
        ).select_related('employee__user')

        # Prepare member data for templates (initials, status, colors)
        member_data = calculate_member_task_statuses(active_project, project_members)
        
        # Get overdue tasks
        overdue_tasks = Task.objects.filter(
            project=active_project,
            due_date__lt=today,
            status__in=['todo', 'in_progress', 'review']
        ).select_related('assigned_to__user')[:5]
        
        # Get recent messages
        recent_messages = Message.objects.filter(
            Q(project=active_project) | 
            Q(sender__in=[member.employee.user for member in project_members]),
            created_at__gte=today - timedelta(days=7)
        ).select_related('sender').order_by('-created_at')[:10]
        
        # Get tasks under review
        review_tasks = Task.objects.filter(
            project=active_project,
            status='review'
        ).select_related('assigned_to__user')[:5]
        
        # Get quick stats for dashboard cards
        active_tasks_count = Task.objects.filter(
            project=active_project,
            status__in=['todo', 'in_progress']
        ).count()
        
        team_members_count = project_members.count()
        
        # Sprint progress
        sprint_progress = 0
        sprint_days_left = 0
        if active_sprint:
            sprint_tasks = Task.objects.filter(sprint=active_sprint)
            total_sprint_points = sprint_tasks.aggregate(
                total=Sum('estimated_hours')
            )['total'] or 0
            completed_sprint_points = sprint_tasks.filter(
                status='done'
            ).aggregate(
                total=Sum('estimated_hours')
            )['total'] or 0
            
            if total_sprint_points > 0:
                sprint_progress = int((completed_sprint_points / total_sprint_points) * 100)
            
            if active_sprint.end_date > today:
                sprint_days_left = (active_sprint.end_date - today).days
            
        
        # Add all context data
        context.update({
            'total_tasks': total_tasks,
            'active_tasks_count': active_tasks_count,
            'completed_tasks': completed_tasks,
            'project_progress': project_progress,
            'active_sprint': active_sprint,
            'sprint_progress': sprint_progress,
            'sprint_days_left': sprint_days_left,
            'project_members': project_members,
            'member_data': member_data,
            'team_members_count': team_members_count,
            'overdue_tasks': overdue_tasks,
            'recent_messages': recent_messages,
            'review_tasks': review_tasks,
            'today': today,
            'available_sprints': available_sprints,
            'all_projects': all_projects,
            'available_employees': available_employees,
        })
    
    return render(request, 'pm/dashboard.html', context)

@login_required
@user_passes_test(is_project_manager, login_url='/login/')
def pm_projects(request):
    """PM Projects List""" 
    current_user = request.user
    
    projects = Project.objects.filter(
        project_manager=current_user
    ).select_related('department').order_by('-created_at')
    
    # Get statistics for each project
    for project in projects:
        project.task_count = Task.objects.filter(project=project).count()
        project.completed_tasks = Task.objects.filter(
            project=project, status='done'
        ).count()
        project.active_tasks = Task.objects.filter(
            project=project, status__in=['todo', 'in_progress']
        ).count()
        
        if project.task_count > 0:
            project.progress_percentage = int((project.completed_tasks / project.task_count) * 100)
        else:
            project.progress_percentage = 0
        
        project.days_remaining_val = project.days_remaining()
    
    context = {
        'user': current_user,
        'projects': projects,
        'today': timezone.now().date(),
    }
    
    return render(request, 'pm/projects.html', context)

@login_required
@user_passes_test(is_project_manager, login_url='/login/')
def pm_project_detail(request, project_id):
    """PM Project Detail View""" 
    project = get_object_or_404(
        Project.objects.select_related('department', 'project_manager'),
        id=project_id,
        project_manager=request.user
    )
    
    # Project tasks
    tasks = Task.objects.filter(project=project).select_related(
        'assigned_to__user', 'sprint'
    ).order_by('-priority', 'due_date')
    
    # Task statistics
    task_stats = {
        'total': tasks.count(),
        'todo': tasks.filter(status='todo').count(),
        'in_progress': tasks.filter(status='in_progress').count(),
        'review': tasks.filter(status='review').count(),
        'done': tasks.filter(status='done').count(),
    }
    
    # Team members
    team_members = ProjectMember.objects.filter(
        project=project, is_active=True
    ).select_related('employee__user')
    
    # Sprints
    sprints = Sprint.objects.filter(project=project).order_by('-start_date')
    
    # Recent activities
    recent_messages = Message.objects.filter(
        project=project
    ).select_related('sender').order_by('-created_at')[:10]
    
    context = {
        'project': project,
        'tasks': tasks,
        'task_stats': task_stats,
        'team_members': team_members,
        'sprints': sprints,
        'recent_messages': recent_messages,
        'today': timezone.now().date(),
    }
    
    return render(request, 'pm/project_detail.html', context)

@login_required
@user_passes_test(is_project_manager, login_url='/login/')
def pm_tasks(request):
    """PM Tasks Management""" 
    current_user = request.user
    today = timezone.now().date()
    week_start = today - timedelta(days=today.weekday())
    week_end = week_start + timedelta(days=6)
    
    # Get all tasks from PM's projects
    managed_projects = Project.objects.filter(project_manager=current_user)
    tasks = Task.objects.filter(
        project__in=managed_projects
    ).select_related(
        'project', 'assigned_to__user', 'sprint'
    ).order_by('-created_at')
    
    # Filter parameters
    status_filter = request.GET.get('status', '')
    priority_filter = request.GET.get('priority', '')
    project_filter = request.GET.get('project', '')
    search_query = request.GET.get('search', '')
    
    if status_filter:
        tasks = tasks.filter(status=status_filter)
    if priority_filter:
        tasks = tasks.filter(priority=priority_filter)
    if project_filter:
        tasks = tasks.filter(project_id=project_filter)
    if search_query:
        tasks = tasks.filter(
            Q(title__icontains=search_query) |
            Q(description__icontains=search_query) |
            Q(task_type__icontains=search_query)
        )
    
    # Calculate statistics
    total_tasks_count = Task.objects.filter(project__in=managed_projects).count()
    active_tasks_count = Task.objects.filter(
        project__in=managed_projects,
        status__in=['todo', 'in_progress']
    ).count()
    overdue_tasks_count = Task.objects.filter(
        project__in=managed_projects,
        due_date__lt=today,
        status__in=['todo', 'in_progress', 'review']
    ).count()
    
    due_this_week_count = Task.objects.filter(
        project__in=managed_projects,
        due_date__range=[today, week_end],
        status__in=['todo', 'in_progress', 'review']
    ).count()
    
    high_priority_week_count = Task.objects.filter(
        project__in=managed_projects,
        due_date__range=[today, week_end],
        priority__in=['high', 'critical'],
        status__in=['todo', 'in_progress', 'review']
    ).count()
    
    completed_tasks_count = Task.objects.filter(
        project__in=managed_projects,
        status='done',
        completed_at__month=today.month,
        completed_at__year=today.year
    ).count()
    
    # Count by status for board view
    todo_tasks_count = tasks.filter(status='todo').count()
    in_progress_tasks_count = tasks.filter(status='in_progress').count()
    review_tasks_count = tasks.filter(status='review').count()
    done_tasks_count = tasks.filter(status='done').count()
    
    # Get recent activity (last 10 updates)
    from core.models import Comment
    recent_comments = Comment.objects.filter(
        task__project__in=managed_projects
    ).select_related('user', 'task').order_by('-created_at')[:10]
    
    recent_activity = []
    color_classes = ['dark-teal', 'dark-cyan', 'golden-orange', 'rusty-spice', 'oxidized-iron', 'brown-red']
    
    for comment in recent_comments:
        initials = f"{comment.user.first_name[0]}{comment.user.last_name[0]}" if comment.user.first_name and comment.user.last_name else comment.username[:2].upper()
        color_index = (comment.user.id % len(color_classes))
        color_class = f"bg-{color_classes[color_index]}"
        
        recent_activity.append({
            'user_name': comment.user.get_full_name(),
            'initials': initials,
            'color_class': color_class,
            'action': 'commented on',
            'task_title': comment.task.title,
            'details': comment.content[:50] + '...' if len(comment.content) > 50 else comment.content,
            'timestamp': comment.created_at,
        })
    
    # Add task status changes to activity
    recent_tasks = tasks.filter(updated_at__gte=today-timedelta(days=7))[:10]
    for task in recent_tasks:
        if task.updated_at > task.created_at + timedelta(minutes=5):  # Only if updated after creation
            initials = f"{task.assigned_to.user.first_name[0]}{task.assigned_to.user.last_name[0]}" if task.assigned_to and task.assigned_to.user.first_name and task.assigned_to.user.last_name else 'PM'
            color_index = (task.id % len(color_classes))
            color_class = f"bg-{color_classes[color_index]}"
            
            recent_activity.append({
                'user_name': task.assigned_to.user.get_full_name() if task.assigned_to else 'System',
                'initials': initials,
                'color_class': color_class,
                'action': 'updated',
                'task_title': task.title,
                'details': f'status to {task.get_status_display()}',
                'details_class': f'text-{color_classes[color_index]}',
                'timestamp': task.updated_at,
            })
    
    # Sort activity by timestamp
    recent_activity.sort(key=lambda x: x['timestamp'], reverse=True)
    
    context = {
        'tasks': tasks,
        'managed_projects': managed_projects,
        'status_filter': status_filter,
        'priority_filter': priority_filter,
        'project_filter': project_filter,
        'today': today,
        'total_tasks_count': total_tasks_count,
        'active_tasks_count': active_tasks_count,
        'overdue_tasks_count': overdue_tasks_count,
        'due_this_week_count': due_this_week_count,
        'high_priority_week_count': high_priority_week_count,
        'completed_tasks_count': completed_tasks_count,
        'todo_tasks_count': todo_tasks_count,
        'in_progress_tasks_count': in_progress_tasks_count,
        'review_tasks_count': review_tasks_count,
        'done_tasks_count': done_tasks_count,
        'recent_activity': recent_activity[:5],  # Limit to 5 most recent
    }
    # Compute subtask-based progress for tasks shown in PM views
    try:
        for task in tasks:
            try:
                subtasks_qs = task.subtasks.all()
                total = subtasks_qs.count()
                completed = subtasks_qs.filter(is_completed=True).count()
                if total > 0:
                    task.subtasks_total = total
                    task.subtasks_completed = completed
                    task.progress = int((completed / total) * 100)
                else:
                    task.subtasks_total = 0
                    task.subtasks_completed = 0
                    task.progress = int(task.progress or 0)
            except Exception:
                task.subtasks_total = 0
                task.subtasks_completed = 0
                task.progress = int(task.progress or 0)
    except Exception:
        pass
    
    return render(request, 'pm/tasks.html', context)



@login_required
@user_passes_test(is_project_manager, login_url='/login/')
def pm_sprints(request):
    """PM Sprints Management""" 
    current_user = request.user
    today = timezone.now().date()
    
    # Get all sprints from PM's projects
    managed_projects = Project.objects.filter(project_manager=current_user)
    sprints = Sprint.objects.filter(
        project__in=managed_projects
    ).select_related('project').order_by('-start_date')
    
    # Calculate sprint statistics
    for sprint in sprints:
        sprint_tasks = Task.objects.filter(sprint=sprint)
        sprint.total_tasks = sprint_tasks.count()
        sprint.completed_tasks = sprint_tasks.filter(status='done').count()
        sprint.in_progress_tasks = sprint_tasks.filter(status='in_progress').count()
        
        if sprint.total_tasks > 0:
            sprint.progress = int((sprint.completed_tasks / sprint.total_tasks) * 100)
        else:
            sprint.progress = 0
        
        if sprint.end_date > today:
            sprint.days_left = (sprint.end_date - today).days
        else:
            sprint.days_left = 0
    
    context = {
        'sprints': sprints,
        'managed_projects': managed_projects,
        'today': today,
    }
    
    return render(request, 'pm/sprints.html', context)



@login_required
@user_passes_test(is_project_manager, login_url='/login/')
def pm_reports(request):
    """PM Reports and Analytics""" 
    current_user = request.user
    today = timezone.now().date()
    thirty_days_ago = today - timedelta(days=30)
    
    # Get PM's projects
    projects = Project.objects.filter(project_manager=current_user)
    
    # Project completion stats
    project_stats = []
    for project in projects:
        total_tasks = Task.objects.filter(project=project).count()
        completed_tasks = Task.objects.filter(project=project, status='done').count()
        overdue_tasks = Task.objects.filter(
            project=project,
            due_date__lt=today,
            status__in=['todo', 'in_progress', 'review']
        ).count()
        
        project_stats.append({
            'project': project,
            'total_tasks': total_tasks,
            'completed_tasks': completed_tasks,
            'overdue_tasks': overdue_tasks,
            'progress': int((completed_tasks / total_tasks * 100)) if total_tasks > 0 else 0,
        })
    
    # Team productivity (last 30 days)
    time_logs = TimeLog.objects.filter(
        date__gte=thirty_days_ago,
        task__project__in=projects
    ).values('employee__user__first_name', 'employee__user__last_name').annotate(
        total_hours=Sum('hours')
    ).order_by('-total_hours')
    
    # Task completion trend
    daily_completions = Task.objects.filter(
        project__in=projects,
        completed_at__gte=thirty_days_ago
    ).extra({'date': "date(completed_at)"}).values('date').annotate(
        count=Count('id')
    ).order_by('date')
    
    context = {
        'project_stats': project_stats,
        'time_logs': time_logs,
        'daily_completions': list(daily_completions),
        'today': today,
        'thirty_days_ago': thirty_days_ago,
    }
    
    return render(request, 'pm/reports.html', context)

# API Views for AJAX operations
@login_required
@user_passes_test(is_project_manager, login_url='/login/')
def update_task_status(request):
    """Update task status via AJAX""" 
    if request.method == 'POST':
        task_id = request.POST.get('task_id')
        new_status = request.POST.get('status')
        
        try:
            task = Task.objects.get(id=task_id)
            
            # Verify the task belongs to PM's project
            if task.project.project_manager != request.user:
                return JsonResponse({'success': False, 'error': 'Permission denied'})
            
            task.status = new_status
            if new_status == 'done':
                task.completed_at = timezone.now()
            task.save()
            
            # Create notification
            Notification.objects.create(
                user=task.assigned_to.user if task.assigned_to else task.project.project_manager,
                notification_type='task_updated',
                title=f'Task Updated: {task.title}',
                message=f'Task status changed to {task.get_status_display()}',
                related_id=task.id,
                related_type='task'
            )
            
            return JsonResponse({'success': True})
        except Task.DoesNotExist:
            return JsonResponse({'success': False, 'error': 'Task not found'})
    
    return JsonResponse({'success': False, 'error': 'Invalid request'})

@login_required
@user_passes_test(is_project_manager, login_url='/login/')
def add_team_member(request):
    """Add team member to project via AJAX""" 
    if request.method == 'POST':
        project_id = request.POST.get('project_id')
        employee_id = request.POST.get('employee_id')
        role = request.POST.get('role')
        
        try:
            project = Project.objects.get(id=project_id, project_manager=request.user)
            employee = EmployeeProfile.objects.get(id=employee_id)
            
            # Check if already a member
            existing_member = ProjectMember.objects.filter(
                project=project, employee=employee
            ).first()
            
            if existing_member:
                existing_member.is_active = True
                existing_member.role = role
                existing_member.save()
            else:
                ProjectMember.objects.create(
                    project=project,
                    employee=employee,
                    role=role,
                    is_active=True
                )
            
            # Create notification
            Notification.objects.create(
                user=employee.user,
                notification_type='task_assigned',
                title=f'Added to Project: {project.name}',
                message=f'You have been added to project {project.name} as {role}',
                related_id=project.id,
                related_type='project'
            )
            
            return JsonResponse({'success': True})
        except Exception as e:
            return JsonResponse({'success': False, 'error': str(e)})
    
    return JsonResponse({'success': False, 'error': 'Invalid request'})

# views/pm_api_views.py
from django.shortcuts import get_object_or_404
from django.contrib.auth.decorators import login_required, user_passes_test
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from django.utils import timezone
import json
from datetime import datetime, timedelta
from core.models import (
    Task, Sprint, Project, ProjectMember,
    EmployeeProfile, User, Notification, Message
)

def is_project_manager(user):
    return user.is_authenticated and (user.role == 'pm' or user.role == 'admin')

@login_required
@user_passes_test(is_project_manager, login_url='/login/')
@require_POST
def create_task_api(request):
    """API endpoint to create a new task"""
    try:
        print(f"[create_task_api] CONTENT_TYPE={request.META.get('CONTENT_TYPE')}")
        print(f"[create_task_api] raw body: {request.body!r}")
        try:
            data = json.loads(request.body)
        except Exception as e:
            # If body isn't JSON, try parsing POST form data
            print(f"[create_task_api] JSON load failed: {e}")
            data = {}
            for k in request.POST:
                data[k] = request.POST.get(k)
            print(f"[create_task_api] parsed from POST: {data}")
        
        # Validate required fields
        required_fields = ['title', 'project_id', 'due_date', 'estimated_hours']
        for field in required_fields:
            if field not in data or not data[field]:
                return JsonResponse({
                    'success': False,
                    'error': f'Missing required field: {field}'
                })
        
        # Get project
        project = get_object_or_404(
            Project, 
            id=data['project_id'],
            project_manager=request.user  # Ensure PM owns the project
        )
        
        # Normalize assigned_to: accept numeric id, null/empty, or the string 'all'
        assigned_raw = data.get('assigned_to')
        assigned_val = None
        print(f"[create_task_api] assigned_raw={assigned_raw!r}")
        if assigned_raw is None or (isinstance(assigned_raw, str) and assigned_raw.strip() == ''):
            assigned_val = None
        else:
            # detect 'all' (case-insensitive, allow surrounding whitespace)
            if isinstance(assigned_raw, str) and assigned_raw.strip().lower() == 'all':
                assigned_val = 'all'
            else:
                # try cast to int for assigned_to_id
                try:
                    assigned_val = int(assigned_raw)
                except Exception:
                    return JsonResponse({'success': False, 'error': f"Invalid assigned_to value: {assigned_raw}"})
        print(f"[create_task_api] assigned_val={assigned_val!r}")
        created_task_ids = []

        if assigned_val == 'all':
            # Create one task per active project member
            members = ProjectMember.objects.filter(project=project, is_active=True).select_related('employee__user')
            for member in members:
                t = Task.objects.create(
                    title=data['title'],
                    description=data.get('description', ''),
                    project=project,
                    assigned_to=member.employee,
                    task_type=data.get('task_type', 'feature'),
                    priority=data.get('priority', 'medium'),
                    estimated_hours=data['estimated_hours'],
                    due_date=datetime.strptime(data['due_date'], '%Y-%m-%d').date(),
                    status='todo',
                    progress=0,
                    actual_hours=0,
                    created_by=request.user,
                    created_at=timezone.now(),
                    updated_at=timezone.now()
                )

                # Add to sprint if specified
                if data.get('sprint_id'):
                    sprint = get_object_or_404(Sprint, id=data['sprint_id'], project=project)
                    t.sprint = sprint
                    t.save()

                # Notify each member
                try:
                    Notification.objects.create(
                        user=member.employee.user,
                        notification_type='task_assigned',
                        title=f'New Task Assigned: {t.title}',
                        message=f'You have been assigned a new task: {t.title}',
                        related_id=t.id,
                        related_type='task'
                    )
                except Exception:
                    pass

                created_task_ids.append(t.id)

            return JsonResponse({
                'success': True,
                'message': 'Tasks created and assigned to all active team members',
                'task_ids': created_task_ids
            })

        # Normal single-assignee flow
        # Ensure assigned_val is an integer id or None before passing to assigned_to_id
        if isinstance(assigned_val, str) and assigned_val.lower() == 'all':
            # defensive: treat as all
            assigned_val = 'all'

        assigned_for_db = assigned_val if isinstance(assigned_val, int) else None
        print(f"[create_task_api] assigned_for_db={assigned_for_db!r}")

        # Resolve EmployeeProfile object if an id was provided
        assigned_employee = None
        if assigned_for_db is not None:
            assigned_employee = EmployeeProfile.objects.filter(id=assigned_for_db).first()

        try:
            task = Task.objects.create(
                title=data['title'],
                description=data.get('description', ''),
                project=project,
                assigned_to=assigned_employee,
                task_type=data.get('task_type', 'feature'),
                priority=data.get('priority', 'medium'),
                estimated_hours=data['estimated_hours'],
                due_date=datetime.strptime(data['due_date'], '%Y-%m-%d').date(),
                status='todo',
                progress=0,
                actual_hours=0,
                created_by=request.user,
                created_at=timezone.now(),
                updated_at=timezone.now()
            )
        except Exception as e:
            print(f"[create_task_api] create single task exception: {e}")
            return JsonResponse({'success': False, 'error': str(e)})

        # Add to sprint if specified
        if data.get('sprint_id'):
            sprint = get_object_or_404(Sprint, id=data['sprint_id'], project=project)
            task.sprint = sprint
            task.save()

        # Create notification if assigned
        if task.assigned_to:
            Notification.objects.create(
                user=task.assigned_to.user,
                notification_type='task_assigned',
                title=f'New Task Assigned: {task.title}',
                message=f'You have been assigned a new task: {task.title}',
                related_id=task.id,
                related_type='task'
            )
        
        return JsonResponse({
            'success': True,
            'message': 'Task created successfully!',
            'task_id': task.id,
            'task_title': task.title
        })
        
    except json.JSONDecodeError:
        return JsonResponse({
            'success': False,
            'error': 'Invalid JSON data'
        })
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': str(e)
        })

@login_required
@user_passes_test(is_project_manager, login_url='/login/')
@require_POST
def start_sprint_api(request):
    """API endpoint to start a new sprint"""
    try:
        data = json.loads(request.body)
        
        # Validate required fields
        required_fields = ['name', 'project_id', 'start_date', 'duration_weeks']
        for field in required_fields:
            if field not in data or not data[field]:
                return JsonResponse({
                    'success': False,
                    'error': f'Missing required field: {field}'
                })
        
        # Get project
        project = get_object_or_404(
            Project,
            id=data['project_id'],
            project_manager=request.user
        )
        
        # Calculate end date
        start_date = datetime.strptime(data['start_date'], '%Y-%m-%d').date()
        duration_days = int(data['duration_weeks']) * 7
        end_date = start_date + timedelta(days=duration_days)
        
        # Create sprint
        sprint = Sprint.objects.create(
            name=data['name'],
            goal=data.get('goal', ''),
            project=project,
            start_date=start_date,
            end_date=end_date,
            status='active',
            created_at=timezone.now(),
            updated_at=timezone.now()
        )
        
        # Add tasks to sprint if specified
        task_ids = data.get('task_ids', [])
        if task_ids:
            tasks = Task.objects.filter(
                id__in=task_ids,
                project=project,
                sprint__isnull=True  # Only add tasks not already in a sprint
            )
            tasks.update(sprint=sprint)
        
        # Notify team members
        team_members = ProjectMember.objects.filter(
            project=project,
            is_active=True
        ).select_related('employee__user')
        
        for member in team_members:
            Notification.objects.create(
                user=member.employee.user,
                notification_type='sprint',
                title=f'New Sprint Started: {sprint.name}',
                message=f'A new sprint "{sprint.name}" has started. Goal: {sprint.goal}',
                related_id=sprint.id,
                related_type='sprint'
            )
        
        return JsonResponse({
            'success': True,
            'message': 'Sprint started successfully!',
            'sprint_id': sprint.id,
            'sprint_name': sprint.name,
            'end_date': end_date.strftime('%Y-%m-%d')
        })
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': str(e)
        })

@login_required
@user_passes_test(is_project_manager, login_url='/login/')
@require_POST
def add_team_member_api(request):
    """API endpoint to add a team member to project"""
    try:
        data = json.loads(request.body)
        
        # Validate required fields
        required_fields = ['project_id', 'employee_id', 'role']
        for field in required_fields:
            if field not in data or not data[field]:
                return JsonResponse({
                    'success': False,
                    'error': f'Missing required field: {field}'
                })
        
        # Get project
        project = get_object_or_404(
            Project,
            id=data['project_id'],
            project_manager=request.user
        )
        
        # Get employee
        employee = get_object_or_404(EmployeeProfile, id=data['employee_id'])
        
        # Check if already a member
        existing_member = ProjectMember.objects.filter(
            project=project,
            employee=employee
        ).first()
        
        if existing_member:
            # Reactivate if previously removed
            existing_member.is_active = True
            existing_member.role = data['role']
            existing_member.save()
            member = existing_member
        else:
            # Create new member
            member = ProjectMember.objects.create(
                project=project,
                employee=employee,
                role=data['role'],
                is_active=True,
                joined_at=timezone.now()
            )
        
        # Create notification for the employee
        Notification.objects.create(
            user=employee.user,
            notification_type='project',
            title=f'Added to Project: {project.name}',
            message=f'You have been added to project "{project.name}" as {member.get_role_display()}',
            related_id=project.id,
            related_type='project'
        )
        
        # Send message to the project channel
        Message.objects.create(
            sender=request.user,
            message_type='announcement',
            subject=f'New Team Member: {employee.user.get_full_name()}',
            content=f'{employee.user.get_full_name()} has joined the project as {member.get_role_display()}',
            project=project,
            is_read=False,
            created_at=timezone.now()
        )
        
        # Get updated team members for response
        team_members = ProjectMember.objects.filter(
            project=project,
            is_active=True
        ).select_related('employee__user')
        
        member_data = []
        for team_member in team_members:
            member_data.append({
                'id': team_member.employee.id,
                'name': team_member.employee.user.get_full_name(),
                'role': team_member.get_role_display(),
                'initials': f"{team_member.employee.user.first_name[0]}{team_member.employee.user.last_name[0]}" 
                if team_member.employee.user.first_name and team_member.employee.user.last_name 
                else team_member.employee.user.username[:2].upper(),
            })
        
        return JsonResponse({
            'success': True,
            'message': 'Team member added successfully!',
            'member_id': member.id,
            'team_members': member_data
        })
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': str(e)
        })

@login_required
@user_passes_test(is_project_manager, login_url='/login/')
@require_POST
def remove_team_member_api(request):
    """API endpoint to remove a team member from project"""
    try:
        data = json.loads(request.body)
        
        # Validate required fields
        required_fields = ['project_id', 'employee_id']
        for field in required_fields:
            if field not in data or not data[field]:
                return JsonResponse({
                    'success': False,
                    'error': f'Missing required field: {field}'
                })
        
        # Get project
        project = get_object_or_404(
            Project,
            id=data['project_id'],
            project_manager=request.user
        )
        
        # Get project member
        project_member = get_object_or_404(
            ProjectMember,
            project=project,
            employee_id=data['employee_id'],
            is_active=True
        )
        
        # Deactivate instead of delete
        project_member.is_active = False
        project_member.save()
        
        # Create notification for the employee
        Notification.objects.create(
            user=project_member.employee.user,
            notification_type='project',
            title=f'Removed from Project: {project.name}',
            message=f'You have been removed from project "{project.name}"',
            related_id=project.id,
            related_type='project'
        )
        
        return JsonResponse({
            'success': True,
            'message': 'Team member removed successfully!'
        })
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': str(e)
        })

@login_required
@user_passes_test(is_project_manager, login_url='/login/')
@require_POST
def approve_task_api(request, task_id=None):
    """API endpoint to approve a task (mark as done).
    Accepts `task_id` either from the URL (`/api/tasks/<id>/approve/`) or
    from the JSON body (`{'task_id': id}`)."""
    try:
        # Try to parse JSON body if present
        data = {}
        if request.body:
            try:
                data = json.loads(request.body)
            except Exception:
                data = {}

        # Determine effective task id: URL param takes precedence
        effective_task_id = task_id or data.get('task_id')
        if not effective_task_id:
            return JsonResponse({
                'success': False,
                'error': 'Missing required field: task_id'
            })

        # Get task
        task = get_object_or_404(Task, id=effective_task_id)
        
        # Verify the task belongs to PM's project
        if task.project.project_manager != request.user:
            return JsonResponse({
                'success': False,
                'error': 'Permission denied'
            })
        
        # Update task
        task.status = 'done'
        task.progress = 100
        task.completed_at = timezone.now()
        task.updated_at = timezone.now()
        task.save()
        
        # Create notification for assignee
        if task.assigned_to:
            Notification.objects.create(
                user=task.assigned_to.user,
                notification_type='task_completed',
                title=f'Task Approved: {task.title}',
                message=f'Your task "{task.title}" has been approved and marked as completed',
                related_id=task.id,
                related_type='task'
            )
        
        return JsonResponse({
            'success': True,
            'message': 'Task approved successfully!',
            'task_id': task.id,
            'task_title': task.title
        })
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': str(e)
        })

@login_required
@user_passes_test(is_project_manager, login_url='/login/')
@require_POST
def request_task_changes_api(request, task_id=None):
    """API endpoint to request changes on a task.
    Accepts `task_id` from the URL or from the JSON body.
    """
    try:
        data = {}
        if request.body:
            try:
                data = json.loads(request.body)
            except Exception:
                data = {}

        # Determine effective task id
        effective_task_id = task_id or data.get('task_id')

        # Validate required fields
        if not effective_task_id:
            return JsonResponse({
                'success': False,
                'error': 'Missing required field: task_id'
            })

        if 'feedback' not in data or not data.get('feedback'):
            return JsonResponse({
                'success': False,
                'error': 'Missing required field: feedback'
            })

        # Get task
        task = get_object_or_404(Task, id=effective_task_id)
        
        # Verify the task belongs to PM's project
        if task.project.project_manager != request.user:
            return JsonResponse({
                'success': False,
                'error': 'Permission denied'
            })
        
        # Update task
        task.status = 'in_progress'  # Send back to in progress
        task.updated_at = timezone.now()
        task.save()
        
        # Create notification for assignee
        if task.assigned_to:
            Notification.objects.create(
                user=task.assigned_to.user,
                notification_type='task_updated',
                title=f'Changes Requested: {task.title}',
                message=f'Changes requested on task "{task.title}": {data["feedback"]}',
                related_id=task.id,
                related_type='task'
            )
        
        # Create comment with feedback
        from core.models import Comment
        Comment.objects.create(
            task=task,
            user=request.user,
            content=f"PM requested changes: {data['feedback']}",
            created_at=timezone.now(),
            updated_at=timezone.now()
        )
        
        return JsonResponse({
            'success': True,
            'message': 'Changes requested successfully!',
            'task_id': task.id
        })
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': str(e)
        })

@login_required
@user_passes_test(is_project_manager, login_url='/login/')
def get_available_tasks_api(request, project_id):
    """API endpoint to get available tasks for sprint planning"""
    try:
        project = get_object_or_404(
            Project,
            id=project_id,
            project_manager=request.user
        )
        
        # Get tasks not in any sprint
        available_tasks = Task.objects.filter(
            project=project,
            sprint__isnull=True,
            status__in=['todo', 'in_progress']
        ).select_related('assigned_to__user').order_by('-priority', 'due_date')
        
        tasks_data = []
        for task in available_tasks:
            tasks_data.append({
                'id': task.id,
                'title': task.title,
                'priority': task.get_priority_display(),
                'priority_class': f'priority-{task.priority}',
                'assigned_to': task.assigned_to.user.get_full_name() if task.assigned_to else 'Unassigned',
                'estimated_hours': float(task.estimated_hours),
                'due_date': task.due_date.strftime('%Y-%m-%d') if task.due_date else None
            })
        
        return JsonResponse({
            'success': True,
            'tasks': tasks_data
        })
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': str(e)
        })

@login_required
@user_passes_test(is_project_manager, login_url='/login/')
def get_available_employees_api(request, project_id):
    """API endpoint to get available employees for team management"""
    try:
        project = get_object_or_404(
            Project,
            id=project_id,
            project_manager=request.user
        )
        mode = request.GET.get('mode', 'available')

        if mode == 'members':
            # Return active project members (for task assignment)
            members = ProjectMember.objects.filter(
                project=project,
                is_active=True
            ).select_related('employee__user')

            employees_data = []
            for member in members:
                emp = member.employee
                employees_data.append({
                    'id': emp.id,
                    'name': emp.user.get_full_name(),
                    'email': emp.user.email,
                    'job_position': emp.job_position,
                    'department': emp.department.name if emp.department else 'No Department',
                    'role': member.role,
                })

            return JsonResponse({
                'success': True,
                'employees': employees_data,
                'allow_assign_all': True
            })

        # Default: return available employees not already on the project (for team management)
        # Get current team members
        current_members = ProjectMember.objects.filter(
            project=project,
            is_active=True
        ).values_list('employee_id', flat=True)
        
        # Get available employees (not in project, active status)
        available_employees = EmployeeProfile.objects.filter(
            status='active'
        ).exclude(
            id__in=current_members
        ).select_related('user', 'department').order_by('user__last_name')
        
        employees_data = []
        for employee in available_employees:
            employees_data.append({
                'id': employee.id,
                'name': employee.user.get_full_name(),
                'email': employee.user.email,
                'job_position': employee.job_position,
                'department': employee.department.name if employee.department else 'No Department',
                'skills': employee.skills or 'No skills specified'
            })
        
        return JsonResponse({
            'success': True,
            'employees': employees_data
        })
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': str(e)
        })

@login_required
@user_passes_test(is_project_manager, login_url='/login/')
@require_POST
def schedule_meeting_api(request):
    """API endpoint to schedule a team meeting"""
    try:
        data = json.loads(request.body)
        
        # Validate required fields
        required_fields = ['title', 'date', 'time', 'project_id']
        for field in required_fields:
            if field not in data or not data[field]:
                return JsonResponse({
                    'success': False,
                    'error': f'Missing required field: {field}'
                })
        
        # Get project
        project = get_object_or_404(
            Project,
            id=data['project_id'],
            project_manager=request.user
        )
        
        # Get team members
        team_members = ProjectMember.objects.filter(
            project=project,
            is_active=True
        ).select_related('employee__user')
        
        # Create message/announcement
        meeting_datetime = f"{data['date']} {data['time']}"
        message = Message.objects.create(
            sender=request.user,
            message_type='announcement',
            subject=f'Team Meeting: {data["title"]}',
            content=f'Team meeting scheduled for {meeting_datetime}. Agenda: {data.get("agenda", "General discussion")}',
            project=project,
            is_read=False,
            created_at=timezone.now()
        )
        
        # Add recipients
        for member in team_members:
            message.recipients.add(member.employee.user)
        
        # Create notifications
        for member in team_members:
            Notification.objects.create(
                user=member.employee.user,
                notification_type='project',
                title=f'Team Meeting Scheduled: {data["title"]}',
                message=f'Team meeting scheduled for {meeting_datetime}',
                related_id=message.id,
                related_type='message'
            )
        
        return JsonResponse({
            'success': True,
            'message': 'Team meeting scheduled successfully!',
            'meeting_title': data['title'],
            'meeting_datetime': meeting_datetime
        })
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': str(e)
        })
@login_required
@user_passes_test(is_project_manager, login_url='/login/')
def pm_projects(request):
    """PM Projects List"""
    current_user = request.user
    today = timezone.now().date()
    
    # Get projects managed by this PM
    projects = Project.objects.filter(
        project_manager=current_user
    ).select_related('department').order_by('-created_at')
    
    # Color classes for member avatars
    color_classes = ['dark-teal', 'dark-cyan', 'golden-orange', 'rusty-spice', 'oxidized-iron', 'brown-red']
    
    # Get statistics for each project
    for project in projects:
        project.task_count = Task.objects.filter(project=project).count()
        project.completed_tasks = Task.objects.filter(
            project=project, status='done'
        ).count()
        project.active_tasks = Task.objects.filter(
            project=project, status__in=['todo', 'in_progress']
        ).count()
        
        if project.task_count > 0:
            project.progress_percentage = int((project.completed_tasks / project.task_count) * 100)
        else:
            project.progress_percentage = 0
        
        project.days_remaining_val = project.days_remaining()
        
        # Get team members for this project
        project_members = ProjectMember.objects.filter(
            project=project, is_active=True
        ).select_related('employee__user')[:6]
        
        project.team_members_count = ProjectMember.objects.filter(
            project=project, is_active=True
        ).count()
        
        # Prepare recent members data for avatars
        recent_members = []
        for i, member in enumerate(project_members[:4]):
            user = member.employee.user
            initials = f"{user.first_name[0]}{user.last_name[0]}" if user.first_name and user.last_name else user.username[:2].upper()
            color = color_classes[i % len(color_classes)]
            recent_members.append({
                'initials': initials,
                'color': color,
                'name': user.get_full_name()
            })
        
        project.recent_members = recent_members
    
    # Calculate project status statistics
    total_projects = projects.count()
    active_projects = projects.filter(status='active').count()
    completed_projects = projects.filter(status='completed').count()
    on_hold_planning_projects = projects.filter(status__in=['on_hold', 'planning']).count()
    
    context = {
        'user': current_user,
        'projects': projects,
        'today': today,
        'total_projects': total_projects,
        'active_projects': active_projects,
        'completed_projects': completed_projects,
        'on_hold_planning_projects': on_hold_planning_projects,
    }
    
    return render(request, 'pm/projects.html', context)

@login_required
@user_passes_test(is_project_manager, login_url='/login/')
def pm_team(request):
    """PM Team Management"""
    current_user = request.user
    
    # Get all team members from PM's projects
    managed_projects = Project.objects.filter(project_manager=current_user)
    team_members = ProjectMember.objects.filter(
        project__in=managed_projects,
        is_active=True
    ).select_related('employee__user', 'project').distinct()
    
    # Get available employees (not in any of PM's projects)
    all_employees = EmployeeProfile.objects.filter(
        status='active'
    ).select_related('user', 'department').exclude(
        id__in=team_members.values_list('employee_id', flat=True)
    )
    
    # Color classes for avatars
    color_classes = ['dark-teal', 'dark-cyan', 'golden-orange', 'rusty-spice', 'oxidized-iron', 'brown-red']
    
    # Project color mapping
    project_colors = {}
    for i, project in enumerate(managed_projects):
        project_colors[project.id] = color_classes[i % len(color_classes)]
    
    # Process team members data
    processed_members = []
    member_data_dict = {}
    
    # First, group by employee (since an employee can be in multiple projects)
    for member in team_members:
        employee = member.employee
        user = employee.user
        
        if employee.id not in member_data_dict:
            # Get user initials
            initials = f"{user.first_name[0]}{user.last_name[0]}" if user.first_name and user.last_name else user.username[:2].upper()
            
            # Get active tasks count
            active_tasks = Task.objects.filter(
                assigned_to=employee,
                status__in=['todo', 'in_progress', 'review']
            ).count()
            
            # Calculate workload percentage (max 10 tasks = 100%)
            workload_percentage = min(100, (active_tasks / 10) * 100) if active_tasks > 0 else 0
            
            # Determine workload status and color
            if active_tasks == 0:
                workload_status = 'available'
                workload_color = 'bg-dark-cyan'
            elif active_tasks <= 3:
                workload_status = 'available'
                workload_color = 'bg-dark-cyan'
            elif active_tasks <= 6:
                workload_status = 'busy'
                workload_color = 'bg-golden-orange'
            else:
                workload_status = 'away'
                workload_color = 'bg-rusty-spice'
            
            # Determine color class for avatar
            color_index = (employee.id % len(color_classes))
            color_class = f"bg-{color_classes[color_index]}"
            
            member_data_dict[employee.id] = {
                'employee_id': employee.id,
                'name': user.get_full_name(),
                'initials': initials,
                'email': user.email,
                'job_position': employee.job_position or 'Not specified',
                'role': member.role,  # Use the first role found
                'role_display': member.get_role_display(),
                'projects': [],
                'project_ids': [],
                'project_count': 0,
                'primary_project_id': member.project.id,  # Store first project ID for filtering
                'active_tasks': active_tasks,
                'workload_percentage': int(workload_percentage),
                'workload_status': workload_status,
                'workload_color': workload_color,
                'color_class': color_class,
                'employee': employee,
            }
        
        # Add project info to this employee
        project_info = {
            'id': member.project.id,
            'name': member.project.name,
            'initials': member.project.name[:2].upper(),
            'color': project_colors.get(member.project.id, 'gray-400'),
        }
        
        member_data_dict[employee.id]['projects'].append(project_info)
        member_data_dict[employee.id]['project_ids'].append(str(member.project.id))
        member_data_dict[employee.id]['project_count'] += 1
        
        # Update role if different (use the most common role or keep as is)
        # For simplicity, we'll keep the first role
    
    # Convert dict to list
    processed_members = list(member_data_dict.values())
    
    # Calculate statistics
    total_team_members = len(processed_members)
    
    # Count by role
    developers_count = sum(1 for m in processed_members if m['role'] in ['dev', 'developer'])
    designers_count = sum(1 for m in processed_members if m['role'] in ['designer', 'ui_ux'])
    qa_count = sum(1 for m in processed_members if m['role'] in ['qa', 'tester'])
    
    # Count available members (workload_status = 'available')
    available_members_count = sum(1 for m in processed_members if m['workload_status'] == 'available')
    available_developers = sum(1 for m in processed_members if m['role'] in ['dev', 'developer'] and m['workload_status'] == 'available')
    available_designers = sum(1 for m in processed_members if m['role'] in ['designer', 'ui_ux'] and m['workload_status'] == 'available')
    available_qa = sum(1 for m in processed_members if m['role'] in ['qa', 'tester'] and m['workload_status'] == 'available')
    
    context = {
        'team_members': processed_members,
        'available_employees': all_employees,
        'managed_projects': managed_projects,
        'total_team_members': total_team_members,
        'developers_count': developers_count,
        'designers_count': designers_count,
        'qa_count': qa_count,
        'available_members_count': available_members_count,
        'available_developers': available_developers,
        'available_designers': available_designers,
        'available_qa': available_qa,
    }
    
    return render(request, 'pm/team.html', context)
@login_required
@user_passes_test(is_project_manager, login_url='/login/')
def get_team_member_details(request, project_id, employee_id):
    """API endpoint to get team member details for a specific project"""
    try:
        # Get project
        project = get_object_or_404(
            Project,
            id=project_id,
            project_manager=request.user
        )
        
        # Get team member
        project_member = get_object_or_404(
            ProjectMember,
            project=project,
            employee_id=employee_id,
            is_active=True
        )
        
        # Get employee details
        employee = project_member.employee
        user = employee.user

        # Get tasks assigned to this employee for the project
        assigned_tasks_qs = Task.objects.filter(project=project, assigned_to=employee).order_by('due_date')
        assigned_tasks = []
        for t in assigned_tasks_qs:
            assigned_tasks.append({
                'id': t.id,
                'title': t.title,
                'status': t.status,
                'status_display': t.get_status_display(),
                'progress': t.progress or 0,
                'estimated_hours': float(t.estimated_hours) if t.estimated_hours is not None else None,
                'due_date': t.due_date.strftime('%Y-%m-%d') if t.due_date else None,
            })

        return JsonResponse({
            'success': True,
            'employee_id': employee.id,
            'employee_name': user.get_full_name(),
            'project_id': project.id,
            'project_name': project.name,
            'role': project_member.role,
            'role_display': project_member.get_role_display(),
            'joined_at': project_member.joined_at.strftime('%Y-%m-%d') if project_member.joined_at else None,
            'email': user.email,
            'position': employee.job_position or 'Not specified',
            'department': employee.department.name if employee.department else 'No department',
            'assigned_tasks': assigned_tasks,
        })
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': str(e)
        })

#tasks
@login_required
@user_passes_test(is_project_manager, login_url='/login/')
def get_task_details_api(request, task_id):
    """API endpoint to get task details"""
    try:
        task = get_object_or_404(Task, id=task_id)
        
        # Verify the task belongs to PM's project
        if task.project.project_manager != request.user:
            return JsonResponse({'success': False, 'error': 'Permission denied'})
        
        return JsonResponse({
            'success': True,
            'task_id': task.id,
            'task_title': task.title,
            'description': task.description,
            'project_name': task.project.name,
            'assignee': task.assigned_to.user.get_full_name() if task.assigned_to else None,
            'assignee_id': task.assigned_to.id if task.assigned_to else None,
            'priority': task.priority,
            'priority_display': task.get_priority_display(),
            'status': task.status,
            'status_display': task.get_status_display(),
            'due_date': task.due_date.strftime('%Y-%m-%d') if task.due_date else None,
            'estimated_hours': float(task.estimated_hours) if task.estimated_hours else None,
            'actual_hours': float(task.actual_hours) if task.actual_hours else None,
            'progress': task.progress or 0,
            'task_type': task.task_type,
            'created_at': task.created_at.strftime('%Y-%m-%d %H:%M') if task.created_at else None,
            'completed_at': task.completed_at.strftime('%Y-%m-%d %H:%M') if task.completed_at else None,
        })
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': str(e)
        })

@login_required
@user_passes_test(is_project_manager, login_url='/login/')
@require_POST
def delete_task_api(request):
    """API endpoint to delete a task"""
    try:
        data = json.loads(request.body)
        
        # Validate required fields
        if 'task_id' not in data or not data['task_id']:
            return JsonResponse({
                'success': False,
                'error': 'Missing required field: task_id'
            })
        
        # Get task
        task = get_object_or_404(Task, id=data['task_id'])
        
        # Verify the task belongs to PM's project
        if task.project.project_manager != request.user:
            return JsonResponse({
                'success': False,
                'error': 'Permission denied'
            })
        
        # Delete task
        task_id = task.id
        task_title = task.title
        task.delete()
        
        # Create notification for assignee if they exist
        if task.assigned_to:
            Notification.objects.create(
                user=task.assigned_to.user,
                notification_type='task_updated',
                title=f'Task Deleted: {task_title}',
                message=f'Task "{task_title}" has been deleted',
                related_id=task_id,
                related_type='task'
            )
        
        return JsonResponse({
            'success': True,
            'message': 'Task deleted successfully!',
            'task_id': task_id
        })
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': str(e)
        })
@login_required
@user_passes_test(is_project_manager, login_url='/login/')
@require_POST
def update_task_api(request, task_id):
    """API endpoint to update a task"""
    try:
        data = json.loads(request.body)
        
        # Get task
        task = get_object_or_404(Task, id=task_id)
        
        # Verify the task belongs to PM's project
        if task.project.project_manager != request.user:
            return JsonResponse({
                'success': False,
                'error': 'Permission denied'
            })
        
        # Update task fields
        if 'title' in data:
            task.title = data['title']
        if 'description' in data:
            task.description = data['description']
        if 'project_id' in data:
            project = get_object_or_404(Project, id=data['project_id'], project_manager=request.user)
            task.project = project
        if 'assigned_to' in data:
            if data['assigned_to']:
                employee = get_object_or_404(EmployeeProfile, id=data['assigned_to'])
                task.assigned_to = employee
            else:
                task.assigned_to = None
        if 'task_type' in data:
            task.task_type = data['task_type']
        if 'priority' in data:
            task.priority = data['priority']
        if 'due_date' in data:
            task.due_date = datetime.strptime(data['due_date'], '%Y-%m-%d').date()
        if 'status' in data:
            old_status = task.status
            task.status = data['status']
            if data['status'] == 'done' and old_status != 'done':
                task.completed_at = timezone.now()
        if 'estimated_hours' in data:
            task.estimated_hours = data['estimated_hours']
        if 'actual_hours' in data:
            task.actual_hours = data['actual_hours']
        if 'progress' in data:
            task.progress = data['progress']
        
        task.updated_at = timezone.now()
        task.save()
        
        # Create notification for assignee if changed
        if 'assigned_to' in data and task.assigned_to:
            Notification.objects.create(
                user=task.assigned_to.user,
                notification_type='task_updated',
                title=f'Task Updated: {task.title}',
                message=f'Task "{task.title}" has been updated',
                related_id=task.id,
                related_type='task'
            )
        
        return JsonResponse({
            'success': True,
            'message': 'Task updated successfully!',
            'task_id': task.id,
            'task_title': task.title
        })
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': str(e)
        })

@login_required
@user_passes_test(is_project_manager, login_url='/login/')
def get_task_details_api(request, task_id):
    """API endpoint to get task details"""
    try:
        task = get_object_or_404(
            Task.objects.select_related('project', 'assigned_to__user'),
            id=task_id
        )
        
        # Verify the task belongs to PM's project
        if task.project.project_manager != request.user:
            return JsonResponse({
                'success': False,
                'error': 'Permission denied'
            })
        
        # Get project members for assignee dropdown
        project_members = ProjectMember.objects.filter(
            project=task.project,
            is_active=True
        ).select_related('employee__user')
        
        assignee_options = []
        for member in project_members:
            assignee_options.append({
                'id': member.employee.id,
                'name': member.employee.user.get_full_name(),
                'role': member.get_role_display()
            })
        
        return JsonResponse({
            'success': True,
            'task_id': task.id,
            'task_title': task.title,
            'description': task.description or '',
            'project_id': task.project.id,
            'project_name': task.project.name,
            'assignee_id': task.assigned_to.id if task.assigned_to else None,
            'assignee': task.assigned_to.user.get_full_name() if task.assigned_to else None,
            'task_type': task.task_type or 'feature',
            'priority': task.priority,
            'priority_display': task.get_priority_display(),
            'status': task.status,
            'status_display': task.get_status_display(),
            'due_date': task.due_date.strftime('%Y-%m-%d') if task.due_date else None,
            'estimated_hours': float(task.estimated_hours) if task.estimated_hours else 0,
            'actual_hours': float(task.actual_hours) if task.actual_hours else 0,
            'progress': task.progress or 0,
            'created_at': task.created_at.strftime('%Y-%m-%d %H:%M') if task.created_at else None,
            'updated_at': task.updated_at.strftime('%Y-%m-%d %H:%M') if task.updated_at else None,
            'completed_at': task.completed_at.strftime('%Y-%m-%d %H:%M') if task.completed_at else None,
            'assignee_options': assignee_options
        })
        
    except Exception as e:
        return JsonResponse({
            'success': False,
            'error': str(e)
        })
# Add to existing views.py
from datetime import datetime, timedelta
import json
import redis
import os
import logging
from django.db.models import Q, Count, Max
from django.core.paginator import Paginator

# Redis connection
_redis_url = os.environ.get('REDIS_URL', 'redis://127.0.0.1:6379/0')
redis_client = redis.from_url(_redis_url)

@login_required
@user_passes_test(is_project_manager, login_url='/login/')
def pm_messages(request):
    """PM Messages View"""
    current_user = request.user
    today = timezone.now()
    
    # Get all direct messages for this PM
    messages = Message.objects.filter(
        Q(sender=current_user) | Q(recipients=current_user),
        message_type='direct'
    ).distinct().order_by('-created_at')
    
    # Group by conversation (other user)
    conversations = {}
    
    logger = logging.getLogger(__name__)
    for msg in messages:
        try:
            # Determine the other user in conversation
            if msg.sender == current_user:
                recipient = msg.recipients.first()
                other_user = recipient if recipient else None
            else:
                other_user = msg.sender

            if not other_user:
                continue

            # Get employee profile for the other user
            employee = EmployeeProfile.objects.filter(user=other_user).first()

            # Only add once per other_user
            if other_user.id in conversations:
                continue

            try:
                # Safe online/last_seen handling
                is_online = bool(redis_client.exists(f'user_online_{other_user.id}'))
                last_seen_raw = redis_client.get(f'user_last_seen_{other_user.id}')
                if isinstance(last_seen_raw, (bytes, bytearray)):
                    last_seen = last_seen_raw.decode('utf-8', errors='ignore')
                else:
                    last_seen = last_seen_raw

                # Safe message content
                content = msg.content or ''
                if len(content) > 50:
                    last_message = content[:50] + '...'
                else:
                    last_message = content

                unread_count = Message.objects.filter(
                    sender=other_user,
                    recipients=current_user,
                    is_read=False,
                    message_type='direct'
                ).count()

                conversations[other_user.id] = {
                    'user_id': other_user.id,
                    'name': other_user.get_full_name(),
                    'initials': f"{other_user.first_name[0]}{other_user.last_name[0]}" if other_user.first_name and other_user.last_name else other_user.username[:2].upper(),
                    'job_position': getattr(employee, 'job_position', 'Team Member') if employee else 'Team Member',
                    'department': employee.department.name if employee and employee.department else 'No Department',
                    'last_message': last_message,
                    'last_message_time': format_message_time(msg.created_at),
                    'unread_count': unread_count,
                    'is_online': is_online,
                    'last_seen': last_seen,
                    'avatar_color': get_user_color(other_user.id),
                }
            except Exception:
                logger.exception('Failed to build conversation entry for user %s', getattr(other_user, 'id', None))
                continue
        except Exception:
            logger.exception('Error processing message id=%s', getattr(msg, 'id', None))
            continue
    
    # Convert to list and sort by last message time
    conversations_list = list(conversations.values())
    conversations_list.sort(key=lambda x: x['last_message_time'], reverse=True)
    
    # Get managed projects for team members
    managed_projects = Project.objects.filter(project_manager=current_user)
    
    # Get all team members from managed projects
    team_members = []
    for project in managed_projects:
        project_members = ProjectMember.objects.filter(
            project=project,
            is_active=True
        ).select_related('employee__user')
        
        for member in project_members:
            user = member.employee.user
            if user.id != current_user.id:
                # Check if already in team_members
                if not any(m['user_id'] == user.id for m in team_members):
                    # Check if there's an existing conversation
                    has_conversation = any(c['user_id'] == user.id for c in conversations_list)
                    
                    # Check online status
                    is_online = redis_client.exists(f'user_online_{user.id}')
                    
                    team_members.append({
                        'user_id': user.id,
                        'name': user.get_full_name(),
                        'initials': f"{user.first_name[0]}{user.last_name[0]}" if user.first_name and user.last_name else user.username[:2].upper(),
                        'job_position': member.employee.job_position,
                        'department': member.employee.department.name if member.employee.department else 'No Department',
                        'role': member.get_role_display(),
                        'project_name': project.name,
                        'has_conversation': has_conversation,
                        'is_online': is_online,
                        'avatar_color': get_user_color(user.id),
                    })
    
    context = {
        'user': current_user,
        'conversations': conversations_list,
        'team_members': team_members,
        'today': today,
    }
    
    return render(request, 'pm/messages.html', context)

# Helper functions
def format_message_time(timestamp):
    """Format message timestamp for display"""
    if not timestamp:
        return ''
    
    now = timezone.now()
    diff = now - timestamp
    
    if diff.days == 0:
        if diff.seconds < 60:
            return 'Just now'
        elif diff.seconds < 3600:
            minutes = diff.seconds // 60
            return f'{minutes}m ago'
        else:
            hours = diff.seconds // 3600
            return f'{hours}h ago'
    elif diff.days == 1:
        return 'Yesterday'
    elif diff.days < 7:
        return f'{diff.days}d ago'
    else:
        return timestamp.strftime('%b %d')

def get_user_color(user_id):
    """Get consistent color for user avatar"""
    colors = ['dark-teal', 'dark-cyan', 'golden-orange', 'rusty-spice', 'oxidized-iron', 'brown-red']
    return f"bg-{colors[user_id % len(colors)]}"
@login_required
@user_passes_test(is_project_manager, login_url='/login/')
def pm_task_reviews(request):
    """PM Task Reviews & Approvals"""
    current_user = request.user
    today = timezone.now().date()
    
    # Get tasks in review status from PM's projects
    managed_projects = Project.objects.filter(project_manager=current_user)
    review_tasks = Task.objects.filter(
        project__in=managed_projects,
        status='review'
    ).select_related(
        'project', 'assigned_to__user', 'sprint'
    ).order_by('-due_date')
    
    # Get recently approved/completed tasks (last 7 days)
    recently_approved = Task.objects.filter(
        project__in=managed_projects,
        status='done',
        completed_at__gte=today - timedelta(days=7)
    ).select_related('project', 'assigned_to__user')[:10]
    
    # Get tasks with requested changes
    tasks_with_changes = Task.objects.filter(
        project__in=managed_projects,
        status='in_progress'
    ).select_related('project', 'assigned_to__user')[:10]
    
    # Calculate statistics
    pending_count = review_tasks.count()
    approved_count = Task.objects.filter(
        project__in=managed_projects,
        status='done',
        completed_at__month=today.month
    ).count()
    changes_requested_count = tasks_with_changes.count()
    
    context = {
        'user': current_user,
        'review_tasks': review_tasks,
        'recently_approved': recently_approved,
        'tasks_with_changes': tasks_with_changes,
        'pending_count': pending_count,
        'approved_count': approved_count,
        'changes_requested_count': changes_requested_count,
        'today': today,
        'managed_projects': managed_projects,
    }
    
    return render(request, 'pm/task_reviews.html', context)
