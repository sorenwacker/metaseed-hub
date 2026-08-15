/* Metaseed Hub JavaScript */

// Prevent scroll wheel from changing number input values
document.addEventListener('wheel', function(e) {
    if (e.target.type === 'number') {
        e.target.blur();
    }
}, { passive: true });

// BroadcastChannel for cross-tab entity updates
var entityUpdateChannel = null;
try {
    entityUpdateChannel = new BroadcastChannel('metaseed-entity-updates');
} catch (e) {
    console.log('BroadcastChannel not supported');
}

// Debug form submissions
document.body.addEventListener('htmx:beforeRequest', function(evt) {
    console.log('HTMX beforeRequest:', evt.detail.elt.tagName, evt.detail.path);
});

// Debug form validation
document.addEventListener('submit', function(evt) {
    console.log('Form submit event:', evt.target.action);
    const form = evt.target;
    if (!form.checkValidity()) {
        console.log('Form is invalid');
        // Find which fields are invalid
        form.querySelectorAll(':invalid').forEach(field => {
            console.log('Invalid field:', field.name, field.validationMessage);
        });
    }
}, true);

document.body.addEventListener('htmx:responseError', function(evt) {
    console.error('HTMX responseError:', evt.detail);
});

document.body.addEventListener('htmx:sendError', function(evt) {
    console.error('HTMX sendError:', evt.detail);
});

// HTMX configuration
document.body.addEventListener('htmx:configRequest', function(evt) {
    // Add auth token to requests if available
    const token = localStorage.getItem('auth_token');
    if (token) {
        evt.detail.headers['Authorization'] = 'Bearer ' + token;
    }

    // For inline table cell edits, only send the specific field being edited
    // This prevents HTMX from collecting all form fields from the parent form
    const triggerEl = evt.detail.elt;
    if (triggerEl && triggerEl.classList.contains('cell-input')) {
        const fieldName = triggerEl.name;
        const fieldValue = triggerEl.value;
        // Clear all parameters and only include this field
        evt.detail.parameters = {};
        evt.detail.parameters[fieldName] = fieldValue;
    }
});

// Broadcast entity changes to other tabs (e.g., graph view)
// HTMX dispatches custom events for HX-Trigger headers
document.body.addEventListener('entityChanged', function(evt) {
    console.log('entityChanged event received, broadcasting to other tabs');
    if (entityUpdateChannel) {
        // Extract project ID from current URL
        const match = window.location.pathname.match(/\/projects\/([^/]+)/);
        if (match) {
            entityUpdateChannel.postMessage({
                type: 'entityChanged',
                projectId: match[1]
            });
            console.log('Broadcasted entity change for project:', match[1]);
        }
    }
});

// Also listen for htmx:afterRequest to catch successful saves
document.body.addEventListener('htmx:afterRequest', function(evt) {
    if (evt.detail.successful && evt.detail.xhr) {
        const trigger = evt.detail.xhr.getResponseHeader('HX-Trigger');
        if (trigger && trigger.includes('entityChanged') && entityUpdateChannel) {
            const match = window.location.pathname.match(/\/projects\/([^/]+)/);
            if (match) {
                entityUpdateChannel.postMessage({
                    type: 'entityChanged',
                    projectId: match[1]
                });
                console.log('Broadcasted entity change (via htmx:afterRequest) for project:', match[1]);
            }
        }
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

// Handle showToast events from HTMX responses
document.body.addEventListener('showToast', function(evt) {
    const { message, type } = evt.detail;
    showToast(message, type || 'error');
});

// Show toast notification
function showToast(message, type) {
    let container = document.getElementById('notification-container');
    if (!container) {
        container = document.createElement('div');
        container.id = 'notification-container';
        container.className = 'notification-container';
        document.body.appendChild(container);
    }

    const toast = document.createElement('div');
    toast.className = `notification notification-${type}`;
    toast.textContent = message;
    container.appendChild(toast);

    // Auto-dismiss after 5 seconds
    setTimeout(() => {
        toast.style.opacity = '0';
        setTimeout(() => toast.remove(), 300);
    }, 5000);
}

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

        // Sanitize user_id for selector (escape special chars)
        const safeUserId = CSS.escape(data.user_id || '');
        let item = list.querySelector(`[data-user="${safeUserId}"]`);

        if (data.action === 'joined') {
            if (!item) {
                item = document.createElement('div');
                item.className = 'presence-item';
                item.dataset.user = data.user_id;
                // Use DOM methods instead of innerHTML to prevent XSS
                const dot = document.createElement('span');
                dot.className = 'presence-dot';
                item.appendChild(dot);
                item.appendChild(document.createTextNode(data.user_name || 'Unknown'));
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
        // Use DOM methods instead of innerHTML to prevent XSS
        const strong = document.createElement('strong');
        strong.textContent = (data.user || 'Unknown') + ':';
        msg.appendChild(strong);
        msg.appendChild(document.createTextNode(' ' + (data.content || '')));
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

// Helper to update cell display from input value
function updateCellDisplay(cell) {
    const input = cell.querySelector('.cell-input');
    const display = cell.querySelector('.cell-display');
    if (input && display) {
        const newValue = input.value;
        display.textContent = newValue || 'Click to edit';
        if (newValue) {
            display.classList.remove('placeholder');
        } else {
            display.classList.add('placeholder');
        }
    }
}

// Selecting a block of cells to fill in one go.
//
// The anchor is the last cell clicked without shift; shift-clicking a second
// cell selects the rectangle between them, within one table body. Cells that
// carry no input (the greyed-out parent reference) are never selectable, so a
// rectangle spanning one still applies only where a value may be written.
let bulkAnchor = null;

function clearCellSelection() {
    document.querySelectorAll('.cell-selected').forEach(c => c.classList.remove('cell-selected'));
    document.querySelectorAll('.bulk-apply').forEach(b => { b.hidden = true; });
}

function cellPosition(cell) {
    const row = cell.closest('tr');
    const body = row ? row.closest('tbody') : null;
    if (!body) return null;
    return {
        body: body,
        row: Array.from(body.children).indexOf(row),
        col: Array.from(row.children).indexOf(cell)
    };
}

function selectBlock(anchor, corner) {
    const from = cellPosition(anchor);
    const to = cellPosition(corner);
    if (!from || !to || from.body !== to.body) return;

    clearCellSelection();
    const rowRange = [Math.min(from.row, to.row), Math.max(from.row, to.row)];
    const colRange = [Math.min(from.col, to.col), Math.max(from.col, to.col)];
    let count = 0;
    Array.from(from.body.children).forEach((row, rowIdx) => {
        if (rowIdx < rowRange[0] || rowIdx > rowRange[1]) return;
        Array.from(row.children).forEach((cell, colIdx) => {
            if (colIdx < colRange[0] || colIdx > colRange[1]) return;
            if (!cell.classList.contains('editable-cell')) return;
            cell.classList.add('cell-selected');
            count += 1;
        });
    });

    const section = from.body.closest('.inline-table-section');
    const apply = section ? section.querySelector('.bulk-apply') : null;
    if (apply && count > 1) {
        apply.hidden = false;
        apply.textContent = 'Apply to selection (' + count + ')';
    }
}

async function applyValueToSelection(section) {
    const selected = Array.from(section.querySelectorAll('.cell-selected'));
    const apply = section.querySelector('.bulk-apply');
    if (!selected.length || !apply) return;

    const anchorInput = bulkAnchor ? bulkAnchor.querySelector('.cell-input') : null;
    const value = anchorInput ? anchorInput.value : '';
    const targets = selected.map(cell => {
        const row = cell.closest('tr');
        return { node_id: row ? row.dataset.nodeId : null, field: cell.dataset.col };
    }).filter(t => t.node_id);
    if (!targets.length) return;

    const meta = document.querySelector('meta[name="csrf-token"]');
    const response = await fetch(apply.dataset.applyUrl, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
            'X-Requested-With': 'XMLHttpRequest',
            'X-CSRF-Token': meta ? meta.content : ''
        },
        body: JSON.stringify({ value: value, targets: targets })
    });

    if (!response.ok) {
        // The server refuses a block it cannot apply whole, and says why.
        alert(await response.text());
        return;
    }

    selected.forEach(cell => {
        const input = cell.querySelector('.cell-input');
        if (input) {
            input.value = value;
            updateCellDisplay(cell);
        }
    });
    clearCellSelection();
    document.body.dispatchEvent(new CustomEvent('entityChanged'));
}

document.addEventListener('click', function(e) {
    const apply = e.target.closest('.bulk-apply');
    if (apply) {
        const section = apply.closest('.inline-table-section');
        if (section) applyValueToSelection(section);
        return;
    }

    const cell = e.target.closest('.editable-cell');
    if (!cell) {
        clearCellSelection();
        return;
    }
    if (e.shiftKey && bulkAnchor) {
        e.preventDefault();
        selectBlock(bulkAnchor, cell);
        return;
    }
    clearCellSelection();
    bulkAnchor = cell;
});

// Editable cell handling
document.addEventListener('click', function(e) {
    const cell = e.target.closest('.editable-cell');
    if (!cell) return;
    if (e.shiftKey) return;

    // Don't activate if already editing
    if (cell.classList.contains('editing')) return;

    // Close any other editing cells and update their display
    document.querySelectorAll('.editable-cell.editing').forEach(c => {
        updateCellDisplay(c);
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
            updateCellDisplay(cell);
            cell.classList.remove('editing');
        }, 100);
    }
});

// Handle Enter key to save and move to next cell
document.addEventListener('keydown', function(e) {
    if (e.key === 'Escape' && document.querySelector('.cell-selected')) {
        clearCellSelection();
        return;
    }
    if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
        const cell = e.target.closest ? e.target.closest('.editable-cell') : null;
        const section = cell ? cell.closest('.inline-table-section') : null;
        if (section && section.querySelector('.cell-selected')) {
            e.preventDefault();
            applyValueToSelection(section);
            return;
        }
    }
    if (e.key === 'Enter' && e.target.classList.contains('cell-input')) {
        e.preventDefault();
        const cell = e.target.closest('.editable-cell');
        const row = cell.closest('tr');
        const cells = Array.from(row.querySelectorAll('.editable-cell'));
        const currentIdx = cells.indexOf(cell);

        // Trigger change event to save
        e.target.dispatchEvent(new Event('change', { bubbles: true }));

        // Update display and move to next cell
        updateCellDisplay(cell);
        if (currentIdx < cells.length - 1) {
            cell.classList.remove('editing');
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

// Tree expand/collapse
function toggleTreeNode(btn) {
    var node = btn.closest('.tree-node');
    if (node) {
        node.classList.toggle('collapsed');
    }
}

// Toggle sidebar
function toggleSidebar() {
    var sidebar = document.getElementById('project-sidebar');
    var layout = document.getElementById('project-layout');
    if (sidebar && layout) {
        sidebar.classList.toggle('collapsed');
        layout.classList.toggle('sidebar-collapsed');
    }
}

// Ontology lookup is handled by metaseed's lookup.js
