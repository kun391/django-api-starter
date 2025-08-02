# Deployment Guide

This guide covers deploying the Django API Template to various environments.

## Prerequisites

- Docker and Docker Compose installed
- Access to a cloud provider (AWS, GCP, Azure, etc.)
- Domain name (optional but recommended)

## Environment Setup

### 1. Environment Variables

Create environment-specific `.env` files:

**Production (.env.production)**
```bash
DEBUG=False
SECRET_KEY=your-secure-secret-key
DJANGO_SETTINGS_MODULE=settings.production

# Database
POSTGRES_DB=your_production_db
POSTGRES_USER=your_production_user
POSTGRES_PASSWORD=your_secure_password
POSTGRES_HOST=your_production_db_host
POSTGRES_PORT=5432

# Redis
REDIS_URL=redis://your_redis_host:6379/0

# RabbitMQ
RABBITMQ_URL=amqp://user:password@your_rabbitmq_host:5672/

# CORS
CORS_ALLOWED_ORIGINS=https://yourdomain.com,https://www.yourdomain.com

# Allowed Hosts
ALLOWED_HOSTS=yourdomain.com,www.yourdomain.com,your_ip_address

# External Services
SENTRY_DSN=your_sentry_dsn

# Email
EMAIL_HOST=smtp.yourprovider.com
EMAIL_PORT=587
EMAIL_HOST_USER=your_email@domain.com
EMAIL_HOST_PASSWORD=your_email_password
EMAIL_USE_TLS=True
```

**Staging (.env.staging)**
```bash
DEBUG=False
SECRET_KEY=your-staging-secret-key
DJANGO_SETTINGS_MODULE=settings.staging

# Similar to production but with staging values
```

### 2. Database Setup

#### PostgreSQL
- Use a managed PostgreSQL service (AWS RDS, GCP Cloud SQL, etc.)
- Ensure SSL connections are enabled
- Set up automated backups
- Configure connection pooling

#### Redis
- Use a managed Redis service (AWS ElastiCache, GCP Memorystore, etc.)
- Enable persistence for data durability
- Configure appropriate memory settings

#### RabbitMQ
- Use a managed message broker or deploy RabbitMQ
- Configure appropriate queues and exchanges
- Set up monitoring and alerting

## Deployment Options

### Option 1: Docker Compose (Simple)

1. **Build and deploy:**
   ```bash
   docker-compose -f docker-compose.yml -f docker-compose.prod.yml up -d
   ```

2. **Run migrations:**
   ```bash
   docker-compose exec web python manage.py migrate
   ```

3. **Collect static files:**
   ```bash
   docker-compose exec web python manage.py collectstatic --noinput
   ```

4. **Create superuser:**
   ```bash
   docker-compose exec web python manage.py createsuperuser
   ```

### Option 2: Kubernetes

1. **Create Kubernetes manifests:**
   ```yaml
   # deployment.yaml
   apiVersion: apps/v1
   kind: Deployment
   metadata:
     name: django-api
   spec:
     replicas: 3
     selector:
       matchLabels:
         app: django-api
     template:
       metadata:
         labels:
           app: django-api
       spec:
         containers:
         - name: django-api
           image: your-registry/django-api-template:latest
           ports:
           - containerPort: 8000
           env:
           - name: DJANGO_SETTINGS_MODULE
             value: "settings.production"
           - name: DATABASE_URL
             valueFrom:
               secretKeyRef:
                 name: django-secrets
                 key: database-url
   ```

2. **Apply manifests:**
   ```bash
   kubectl apply -f k8s/
   ```

### Option 3: Cloud Platforms

#### AWS (ECS/Fargate)
1. Create ECR repository
2. Build and push Docker image
3. Create ECS cluster and service
4. Configure load balancer
5. Set up RDS, ElastiCache, and SQS

#### Google Cloud Platform
1. Build and push to Container Registry
2. Deploy to Cloud Run or GKE
3. Configure Cloud SQL, Memorystore, and Pub/Sub

#### Heroku
1. Create Heroku app
2. Add PostgreSQL, Redis, and RabbitMQ add-ons
3. Deploy using Git:
   ```bash
   git push heroku main
   ```

## SSL/TLS Configuration

### Using Let's Encrypt with Certbot
```bash
# Install certbot
sudo apt-get install certbot

# Obtain certificate
sudo certbot certonly --standalone -d yourdomain.com

# Configure nginx or your web server
```

### Using Cloud Provider SSL
- AWS: Application Load Balancer with ACM
- GCP: Cloud Load Balancer with managed SSL
- Azure: Application Gateway with SSL termination

## Monitoring and Logging

### Application Monitoring
- **Sentry**: Error tracking and performance monitoring
- **New Relic**: Application performance monitoring
- **Datadog**: Full-stack monitoring

### Infrastructure Monitoring
- **Prometheus + Grafana**: Metrics collection and visualization
- **ELK Stack**: Log aggregation and analysis
- **CloudWatch**: AWS-native monitoring

### Health Checks
The application includes health check endpoints:
- `/health/` - Basic application health
- `/health/db/` - Database connectivity
- `/health/celery/` - Celery worker status

## Backup Strategy

### Database Backups
```bash
# Automated PostgreSQL backup
pg_dump -h $DB_HOST -U $DB_USER -d $DB_NAME > backup_$(date +%Y%m%d_%H%M%S).sql

# Restore from backup
psql -h $DB_HOST -U $DB_USER -d $DB_NAME < backup_file.sql
```

### File Backups
- Use cloud storage (S3, GCS, Azure Blob) for media files
- Implement automated backup scripts
- Test restore procedures regularly

## Security Considerations

### Environment Security
- Use secrets management (AWS Secrets Manager, GCP Secret Manager)
- Rotate credentials regularly
- Enable audit logging
- Use VPC/private networks

### Application Security
- Keep dependencies updated
- Use HTTPS everywhere
- Implement rate limiting
- Configure CORS properly
- Use security headers

### Database Security
- Enable SSL connections
- Use strong passwords
- Implement connection pooling
- Regular security updates

## Scaling

### Horizontal Scaling
- Deploy multiple application instances
- Use load balancers
- Implement session sharing (Redis)
- Configure auto-scaling policies

### Vertical Scaling
- Increase CPU/memory for instances
- Optimize database queries
- Use caching strategies
- Implement CDN for static files

## Troubleshooting

### Common Issues

1. **Database Connection Issues**
   ```bash
   # Check database connectivity
   python manage.py dbshell
   ```

2. **Static Files Not Loading**
   ```bash
   # Collect static files
   python manage.py collectstatic --noinput
   ```

3. **Celery Tasks Not Running**
   ```bash
   # Check Celery worker status
   celery -A core inspect active
   ```

4. **Memory Issues**
   ```bash
   # Monitor memory usage
   docker stats
   ```

### Log Analysis
```bash
# View application logs
docker-compose logs web

# View specific service logs
docker-compose logs -f web

# Check error logs
tail -f logs/django.log
```

## Performance Optimization

### Database Optimization
- Use database indexes
- Implement query optimization
- Use database connection pooling
- Consider read replicas

### Caching Strategy
- Redis for session storage
- Redis for API response caching
- CDN for static files
- Browser caching headers

### Application Optimization
- Use async tasks for heavy operations
- Implement pagination
- Optimize serializers
- Use database select_related/prefetch_related