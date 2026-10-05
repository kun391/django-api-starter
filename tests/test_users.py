"""
Tests for users app endpoints.
"""

import pytest
from django.urls import reverse
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
            'password': 'newpass123',
            'first_name': 'New',
            'last_name': 'User'
        }
        response = api_client.post(url, data)
        
        assert response.status_code == 201
        assert response.json()['username'] == 'newuser'
        assert response.json()['email'] == 'newuser@example.com'
        assert 'password' not in response.json()
    
    def test_get_user_list_requires_auth(self, api_client):
        """Test that getting user list requires authentication."""
        url = reverse('user-list')
        response = api_client.get(url)
        
        assert response.status_code == 403
    
    def test_get_user_list_authenticated(self, authenticated_client):
        """Test getting user list when authenticated."""
        url = reverse('user-list')
        response = authenticated_client.get(url)
        
        assert response.status_code == 200
        assert 'results' in response.json()
    
    def test_get_current_user(self, authenticated_client, user):
        """Test getting current user information."""
        url = reverse('user-me')
        response = authenticated_client.get(url)
        
        assert response.status_code == 200
        assert response.json()['email'] == user.email
    
    def test_update_current_user(self, authenticated_client, user):
        """Test updating current user information."""
        url = reverse('user-update-me')
        data = {
            'first_name': 'Updated',
            'last_name': 'Name'
        }
        response = authenticated_client.patch(url, data)
        
        assert response.status_code == 200
        assert response.json()['first_name'] == 'Updated'
        assert response.json()['last_name'] == 'Name'
    
    def test_get_user_detail(self, authenticated_client, user):
        """Test getting user detail."""
        url = reverse('user-detail', kwargs={'pk': user.pk})
        response = authenticated_client.get(url)
        
        assert response.status_code == 200
        assert response.json()['email'] == user.email