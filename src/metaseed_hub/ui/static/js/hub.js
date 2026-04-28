/* Metaseed Hub JavaScript */

// HTMX configuration
document.body.addEventListener('htmx:configRequest', function(evt) {
    // Add auth token to requests if available
    const token = localStorage.getItem('auth_token');
    if (token) {
        evt.detail.headers['Authorization'] = 'Bearer ' + token;
    }
});

// Handle notifications
document.body.addEventListener('htmx:afterSwap', function(evt) {
    // Auto-dismiss notifications after 5 seconds
    if (evt.detail.target.id === 'notification-container') {
        const notification = evt.detail.target.lastElementChild;
        if (notification) {
            setTimeout(() => {
                notification.style.opacity = '0';
                setTimeout(() => notification.remove(), 300);
            }, 5000);
        }
    }
});

// WebSocket reconnection helper
class HubWebSocket {
    constructor(projectId) {
        this.projectId = projectId;
        this.ws = null;
        this.reconnectAttempts = 0;
        this.maxReconnectAttempts = 5;
        this.reconnectDelay = 1000;
    }

    connect() {
        const token = localStorage.getItem('auth_token') || '';
        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        const url = `${protocol}//${window.location.host}/ws/${this.projectId}?token=${token}`;

        this.ws = new WebSocket(url);

        this.ws.onopen = () => {
            console.log('WebSocket connected');
            this.reconnectAttempts = 0;
        };

        this.ws.onmessage = (event) => {
            const data = JSON.parse(event.data);
            this.handleMessage(data);
        };

        this.ws.onclose = () => {
            console.log('WebSocket closed');
            this.reconnect();
        };

        this.ws.onerror = (error) => {
            console.error('WebSocket error:', error);
        };
    }

    reconnect() {
        if (this.reconnectAttempts < this.maxReconnectAttempts) {
            this.reconnectAttempts++;
            const delay = this.reconnectDelay * Math.pow(2, this.reconnectAttempts - 1);
            console.log(`Reconnecting in ${delay}ms...`);
            setTimeout(() => this.connect(), delay);
        }
    }

    handleMessage(data) {
        switch (data.type) {
            case 'presence':
                this.updatePresence(data);
                break;
            case 'chat':
                this.appendChat(data);
                break;
            case 'update':
                // Trigger HTMX refresh
                htmx.trigger('#entity-tree', 'refresh');
                break;
        }
    }

    updatePresence(data) {
        const list = document.getElementById('presence-list');
        if (!list) return;

        // Find or create presence item
        let item = list.querySelector(`[data-user="${data.user_id}"]`);

        if (data.action === 'joined') {
            if (!item) {
                item = document.createElement('div');
                item.className = 'presence-item';
                item.dataset.user = data.user_id;
                item.innerHTML = `<span class="presence-dot"></span>${data.user_name}`;
                list.appendChild(item);
            }
        } else if (data.action === 'left' && item) {
            item.remove();
        }
    }

    appendChat(data) {
        const messages = document.getElementById('chat-messages');
        if (!messages) return;

        const msg = document.createElement('div');
        msg.className = 'chat-message';
        msg.innerHTML = `<strong>${data.user}:</strong> ${data.content}`;
        messages.appendChild(msg);
        messages.scrollTop = messages.scrollHeight;
    }

    send(type, payload) {
        if (this.ws && this.ws.readyState === WebSocket.OPEN) {
            this.ws.send(JSON.stringify({ type, ...payload }));
        }
    }

    sendChat(content) {
        this.send('chat', { content });
    }
}

// Export for use in templates
window.HubWebSocket = HubWebSocket;

// Editable cell handling
document.addEventListener('click', function(e) {
    const cell = e.target.closest('.editable-cell');
    if (!cell) return;

    // Don't activate if already editing
    if (cell.classList.contains('editing')) return;

    // Close any other editing cells
    document.querySelectorAll('.editable-cell.editing').forEach(c => {
        c.classList.remove('editing');
    });

    // Activate this cell
    cell.classList.add('editing');
    const input = cell.querySelector('.cell-input');
    if (input) {
        input.focus();
        input.select();
    }
});

// Handle blur to close editing
document.addEventListener('focusout', function(e) {
    const cell = e.target.closest('.editable-cell');
    if (cell && cell.classList.contains('editing')) {
        // Small delay to allow HTMX to process
        setTimeout(() => {
            cell.classList.remove('editing');
            // Update display value
            const input = cell.querySelector('.cell-input');
            const display = cell.querySelector('.cell-display');
            if (input && display) {
                display.textContent = input.value;
            }
        }, 100);
    }
});

// Handle Enter key to save and move to next cell
document.addEventListener('keydown', function(e) {
    if (e.key === 'Enter' && e.target.classList.contains('cell-input')) {
        e.preventDefault();
        const cell = e.target.closest('.editable-cell');
        const row = cell.closest('tr');
        const cells = Array.from(row.querySelectorAll('.editable-cell'));
        const currentIdx = cells.indexOf(cell);

        // Trigger change event to save
        e.target.dispatchEvent(new Event('change', { bubbles: true }));

        // Move to next cell
        if (currentIdx < cells.length - 1) {
            cells[currentIdx + 1].click();
        } else {
            cell.classList.remove('editing');
        }
    } else if (e.key === 'Escape' && e.target.classList.contains('cell-input')) {
        const cell = e.target.closest('.editable-cell');
        cell.classList.remove('editing');
    }
});

// Handle Tab key for cell navigation
document.addEventListener('keydown', function(e) {
    if (e.key === 'Tab' && e.target.classList.contains('cell-input')) {
        const cell = e.target.closest('.editable-cell');
        const row = cell.closest('tr');
        const cells = Array.from(row.querySelectorAll('.editable-cell'));
        const currentIdx = cells.indexOf(cell);

        e.preventDefault();

        // Trigger change to save current
        e.target.dispatchEvent(new Event('change', { bubbles: true }));

        if (e.shiftKey) {
            // Move to previous cell
            if (currentIdx > 0) {
                setTimeout(() => cells[currentIdx - 1].click(), 50);
            }
        } else {
            // Move to next cell
            if (currentIdx < cells.length - 1) {
                setTimeout(() => cells[currentIdx + 1].click(), 50);
            }
        }
    }
});
