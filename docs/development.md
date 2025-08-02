# Development Guide

This guide covers development practices, coding standards, and workflow for the Django API Template.

## Development Environment Setup

### Prerequisites
- Python 3.11+
- Docker and Docker Compose
- Git
- VS Code (recommended) or your preferred IDE

### Initial Setup
1. **Clone the repository:**
   ```bash
   git clone <repository-url>
   cd django-api-template
   ```

2. **Copy environment file:**
   ```bash
   cp env.example .env
   # Edit .env with your local configuration
   ```

3. **Start development environment:**
   ```bash
   make dev
   ```

4. **Apply migrations:**
   ```bash
   make migrate
   ```

5. **Create superuser:**
   ```bash
   make createsuperuser
   ```

## Project Structure

```
django-api-template/
├── apps/                    # Django applications
│   ├── core/               # Core functionality
│   │   ├── health/         # Health check endpoints
│   │   ├── celery.py       # Celery configuration
│   │   ├── urls.py         # Main URL configuration
│   │   └── wsgi.py         # WSGI configuration
│   └── users/              # User management
│       ├── models.py       # User model
│       ├── serializers.py  # DRF serializers
│       ├── views.py        # API views
│       ├── urls.py         # URL patterns
│       └── admin.py        # Admin configuration
├── settings/               # Django settings
│   ├── base.py            # Base settings
│   ├── local.py           # Local development
│   ├── staging.py         # Staging environment
│   └── production.py      # Production environment
├── requirements/           # Python dependencies
│   ├── base.txt           # Base requirements
│   ├── local.txt          # Development requirements
│   └── production.txt     # Production requirements
├── tests/                 # Test configuration
│   ├── conftest.py        # Pytest configuration
│   ├── test_health.py     # Health check tests
│   └── test_users.py      # User app tests
├── docs/                  # Documentation
├── bin/                   # Scripts and utilities
├── config/                # Gunicorn configuration
├── Dockerfile             # Docker configuration
├── docker-compose.yml     # Docker Compose services
├── Makefile               # Development commands
├── pyproject.toml         # Python project configuration
└── manage.py              # Django management script
```

## Coding Standards

### Python Style Guide
- Follow [PEP 8](https://www.python.org/dev/peps/pep-0008/) style guide
- Use type hints for function parameters and return values
- Keep functions small and focused (max 20-30 lines)
- Use descriptive variable and function names
- Write docstrings for all public functions and classes

### Django Best Practices
- Use Django's built-in features when possible
- Follow Django's model field naming conventions
- Use Django's ORM efficiently (avoid N+1 queries)
- Implement proper model relationships
- Use Django's form validation

### API Design Guidelines
- Follow RESTful principles
- Use consistent URL patterns
- Implement proper HTTP status codes
- Use pagination for list endpoints
- Implement filtering and searching
- Version your APIs

### Example Code Structure

```python
# models.py
from django.db import models
from django.contrib.auth.models import AbstractUser


class User(AbstractUser):
    """Custom user model with additional fields."""
    
    email = models.EmailField(unique=True)
    bio = models.TextField(max_length=500, blank=True)
    birth_date = models.DateField(null=True, blank=True)
    
    class Meta:
        verbose_name = 'User'
        verbose_name_plural = 'Users'
    
    def __str__(self) -> str:
        return self.email


# serializers.py
from rest_framework import serializers
from django.contrib.auth import get_user_model

User = get_user_model()


class UserSerializer(serializers.ModelSerializer):
    """Serializer for User model."""
    
    class Meta:
        model = User
        fields = ['id', 'username', 'email', 'first_name', 'last_name']
        read_only_fields = ['id']


# views.py
from rest_framework import viewsets, permissions
from django.contrib.auth import get_user_model
from .serializers import UserSerializer

User = get_user_model()


class UserViewSet(viewsets.ModelViewSet):
    """ViewSet for User model."""
    
    queryset = User.objects.all()
    serializer_class = UserSerializer
    permission_classes = [permissions.IsAuthenticated]
```

## Development Workflow

### Git Workflow
1. **Create feature branch:**
   ```bash
   git checkout -b feature/your-feature-name
   ```

2. **Make changes and commit:**
   ```bash
   git add .
   git commit -m "feat: add user profile endpoint"
   ```

3. **Push and create pull request:**
   ```bash
   git push origin feature/your-feature-name
   ```

### Commit Message Convention
Use [Conventional Commits](https://www.conventionalcommits.org/):
- `feat:` - New feature
- `fix:` - Bug fix
- `docs:` - Documentation changes
- `style:` - Code style changes
- `refactor:` - Code refactoring
- `test:` - Test changes
- `chore:` - Maintenance tasks

### Code Review Process
1. Create pull request with clear description
2. Request review from team members
3. Address feedback and make changes
4. Ensure all tests pass
5. Merge after approval

## Testing

### Test Structure
- Unit tests for models, serializers, and utilities
- Integration tests for views and API endpoints
- End-to-end tests for critical user flows

### Running Tests
```bash
# Run all tests
make test

# Run tests with coverage
make coverage

# Run specific test file
make test-users

# Run tests in parallel
pytest -n auto
```

### Writing Tests
```python
import pytest
from django.urls import reverse
from rest_framework.test import APIClient
from django.contrib.auth import get_user_model

User = get_user_model()


@pytest.mark.django_db
class TestUserEndpoints:
    """Test user endpoints."""
    
    def test_create_user(self, api_client):
        """Test creating a new user."""
        url = reverse('user-list')
        data = {
            'username': 'newuser',
            'email': 'newuser@example.com',
            'password': 'newpass123'
        }
        response = api_client.post(url, data)
        
        assert response.status_code == 201
        assert response.json()['username'] == 'newuser'
```

## Code Quality

### Linting and Formatting
```bash
# Format code
make format

# Run linting checks
make lint

# Run all quality checks
make check
```

### Pre-commit Hooks
Install pre-commit hooks to automatically run quality checks:
```bash
# Install pre-commit
pip install pre-commit

# Install hooks
pre-commit install
```

### Type Checking
```bash
# Run mypy type checking
mypy .
```

## Database Management

### Migrations
```bash
# Create migrations
make makemigrations

# Apply migrations
make migrate

# Show migration status
python manage.py showmigrations
```

### Database Shell
```bash
# Access database shell
make dbshell

# Reset database (development only)
make reset-db
```

## Background Tasks

### Celery Configuration
```python
# apps/core/celery.py
from celery import Celery
import os

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'settings.local')

app = Celery('core')
app.config_from_object('django.conf:settings', namespace='CELERY')
app.autodiscover_tasks()
```

### Running Celery
```bash
# Start Celery worker
make worker

# Start Celery beat scheduler
make beat

# Start Celery monitoring
make flower
```

### Writing Tasks
```python
# tasks.py
from celery import shared_task
from django.core.mail import send_mail


@shared_task
def send_welcome_email(user_id: int) -> None:
    """Send welcome email to new user."""
    from django.contrib.auth import get_user_model
    
    User = get_user_model()
    user = User.objects.get(id=user_id)
    
    send_mail(
        subject='Welcome to our platform!',
        message=f'Hello {user.first_name}, welcome to our platform!',
        from_email='noreply@example.com',
        recipient_list=[user.email],
    )
```

## API Documentation

### Using drf-spectacular
The project uses drf-spectacular for automatic API documentation:

- **Schema:** `/api/schema/`
- **Swagger UI:** `/api/docs/`
- **ReDoc:** `/api/redoc/`

### Documenting Views
```python
from drf_spectacular.utils import extend_schema, OpenApiParameter
from rest_framework import viewsets


@extend_schema(
    tags=['Users'],
    description='User management endpoints',
    responses={200: UserSerializer}
)
class UserViewSet(viewsets.ModelViewSet):
    """ViewSet for User model."""
    
    @extend_schema(
        summary='List users',
        description='Retrieve a list of all users',
        parameters=[
            OpenApiParameter(name='search', description='Search users by name or email')
        ]
    )
    def list(self, request):
        """List all users."""
        return super().list(request)
```

## Performance Optimization

### Database Optimization
- Use `select_related()` and `prefetch_related()` to avoid N+1 queries
- Add database indexes for frequently queried fields
- Use database connection pooling
- Monitor slow queries

### Caching Strategy
- Use Redis for session storage
- Implement API response caching
- Use CDN for static files
- Set appropriate cache headers

### Example Optimizations
```python
# Avoid N+1 queries
users = User.objects.select_related('profile').prefetch_related('posts')

# Use database indexes
class User(AbstractUser):
    email = models.EmailField(unique=True, db_index=True)
    
    class Meta:
        indexes = [
            models.Index(fields=['username']),
            models.Index(fields=['date_joined']),
        ]

# Implement caching
from django.core.cache import cache

def get_user_stats(user_id: int) -> dict:
    """Get user statistics with caching."""
    cache_key = f'user_stats_{user_id}'
    stats = cache.get(cache_key)
    
    if stats is None:
        stats = calculate_user_stats(user_id)
        cache.set(cache_key, stats, timeout=3600)  # Cache for 1 hour
    
    return stats
```

## Security Best Practices

### Input Validation
- Always validate and sanitize user input
- Use Django forms for validation
- Implement proper serializers with validation

### Authentication and Authorization
- Use Django's built-in authentication
- Implement proper permissions
- Use HTTPS in production
- Implement rate limiting

### Example Security Measures
```python
# Rate limiting
from django_ratelimit.decorators import ratelimit

@ratelimit(key='ip', rate='5/m', method='POST')
def create_user(request):
    """Create user with rate limiting."""
    pass

# Permission classes
from rest_framework import permissions

class IsOwnerOrReadOnly(permissions.BasePermission):
    """Custom permission to only allow owners to edit."""
    
    def has_object_permission(self, request, view, obj):
        if request.method in permissions.SAFE_METHODS:
            return True
        return obj.user == request.user
```

## Monitoring and Logging

### Logging Configuration
```python
# settings/base.py
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'verbose': {
            'format': '{levelname} {asctime} {module} {process:d} {thread:d} {message}',
            'style': '{',
        },
    },
    'handlers': {
        'file': {
            'level': 'INFO',
            'class': 'logging.FileHandler',
            'filename': BASE_DIR / 'logs' / 'django.log',
            'formatter': 'verbose',
        },
    },
    'loggers': {
        'django': {
            'handlers': ['file'],
            'level': 'INFO',
            'propagate': False,
        },
    },
}
```

### Health Checks
The application includes health check endpoints:
- `/health/` - Basic application health
- `/health/db/` - Database connectivity
- `/health/celery/` - Celery worker status

## Troubleshooting

### Common Issues

1. **Database Connection Issues**
   ```bash
   # Check database connectivity
   python manage.py dbshell
   
   # Check database settings
   python manage.py check --database default
   ```

2. **Static Files Not Loading**
   ```bash
   # Collect static files
   python manage.py collectstatic --noinput
   
   # Check static files configuration
   python manage.py findstatic admin/css/base.css
   ```

3. **Migration Issues**
   ```bash
   # Show migration status
   python manage.py showmigrations
   
   # Fake migrations if needed
   python manage.py migrate --fake
   ```

4. **Celery Issues**
   ```bash
   # Check Celery worker status
   celery -A core inspect active
   
   # Check Celery configuration
   celery -A core inspect conf
   ```

### Debugging Tools
- Django Debug Toolbar (development)
- Django Extensions shell_plus
- IPython for enhanced shell
- Logging and monitoring tools

## Resources

### Documentation
- [Django Documentation](https://docs.djangoproject.com/)
- [Django REST Framework Documentation](https://www.django-rest-framework.org/)
- [Celery Documentation](https://docs.celeryproject.org/)

### Tools
- [Django Debug Toolbar](https://django-debug-toolbar.readthedocs.io/)
- [Django Extensions](https://django-extensions.readthedocs.io/)
- [drf-spectacular](https://drf-spectacular.readthedocs.io/)

### Best Practices
- [Django Best Practices](https://django-best-practices.readthedocs.io/)
- [Two Scoops of Django](https://www.feldroy.com/books/two-scoops-of-django-3-x)
- [Django REST Framework Best Practices](https://www.django-rest-framework.org/topics/best-practices/)