/**
 * fb_ui: Complete Full-Featured Dashboard Controller
 * Multi-view navigation, Job orchestration, Leads explorer, Session & Proxy pools
 */

class DashboardApp {
  constructor() {
    this.apiKey = localStorage.getItem('fb_crawl_api_key') || 'dev-api-key-1234567890123456789012';
    this.baseUrl = window.location.origin;
    this.currentView = 'dashboard';
    this.refreshInterval = null;

    this.initElements();
    this.bindEvents();
    this.handleRouting();
    this.checkHealth();
    this.loadCurrentViewData();
    this.startAutoRefresh();
  }

  initElements() {
    // Topbar
    this.statusIndicator = document.getElementById('status-indicator');
    this.statusText = document.getElementById('status-text');
    this.viewHeaderTitle = document.getElementById('view-header-title');
    this.btnSettings = document.getElementById('btn-settings');
    this.btnRefresh = document.getElementById('btn-refresh');

    // Navigation
    this.navItems = document.querySelectorAll('.nav-item');
    this.viewSections = document.querySelectorAll('.view-section');

    // Metric Elements (Dashboard)
    this.metricTotalUsers = document.getElementById('metric-total-users');
    this.metricUsersWithPhone = document.getElementById('metric-users-phone');
    this.metricPhoneRate = document.getElementById('metric-phone-rate');
    this.metricTotalJobs = document.getElementById('metric-total-jobs');
    this.metricActiveSessions = document.getElementById('metric-active-sessions');
    this.metricActiveProxies = document.getElementById('metric-active-proxies');

    // Tables
    this.tableRecentJobs = document.getElementById('table-recent-jobs-body');
    this.tableRecentLeads = document.getElementById('table-recent-leads-body');
    this.tableAllJobs = document.getElementById('table-all-jobs-body');
    this.tableAllLeads = document.getElementById('table-all-leads-body');
    this.tableAllSessions = document.getElementById('table-all-sessions-body');
    this.tableAllProxies = document.getElementById('table-all-proxies-body');

    // Modals
    this.modalSettings = document.getElementById('modal-settings');
    this.modalCreateJob = document.getElementById('modal-create-job');
    this.modalJobDetails = document.getElementById('modal-job-details');
    this.modalPhoneEvidence = document.getElementById('modal-phone-evidence');
    this.modalImportSession = document.getElementById('modal-import-session');
    this.modalExtractSession = document.getElementById('modal-extract-session');
    this.modalAddProxies = document.getElementById('modal-add-proxies');

    // Forms
    this.formCreateJob = document.getElementById('form-create-job');
    this.formFilterLeads = document.getElementById('form-filter-leads');
    this.formImportSession = document.getElementById('form-import-session');
    this.formExtractSession = document.getElementById('form-extract-session');
    this.formAddProxies = document.getElementById('form-add-proxies');

    // Settings Input
    this.inputApiKey = document.getElementById('input-api-key');
    if (this.inputApiKey) this.inputApiKey.value = this.apiKey;

    // Export Buttons
    this.btnExportCsv = document.getElementById('btn-export-csv');
    this.btnExportJson = document.getElementById('btn-export-json');

    // Open Modal Buttons
    this.btnOpenCreateJob = document.getElementById('btn-open-create-job');
    this.btnOpenImportSession = document.getElementById('btn-open-import-session');
    this.btnOpenExtractSession = document.getElementById('btn-open-extract-session');
    this.btnOpenAddProxies = document.getElementById('btn-open-add-proxies');
  }

  bindEvents() {
    // Navigation routing
    this.navItems.forEach(item => {
      item.addEventListener('click', (e) => {
        e.preventDefault();
        const view = item.getAttribute('data-view');
        this.switchView(view);
      });
    });

    window.addEventListener('hashchange', () => this.handleRouting());

    // Settings
    if (this.btnSettings) this.btnSettings.addEventListener('click', () => this.openModal(this.modalSettings));
    const btnSaveSettings = document.getElementById('btn-save-settings');
    if (btnSaveSettings) btnSaveSettings.addEventListener('click', () => this.saveSettings());

    // Refresh
    if (this.btnRefresh) {
      this.btnRefresh.addEventListener('click', () => {
        this.loadCurrentViewData();
        this.showToast('Đang làm mới dữ liệu...');
      });
    }

    // Modal open buttons
    if (this.btnOpenCreateJob) this.btnOpenCreateJob.addEventListener('click', () => this.openModal(this.modalCreateJob));
    if (this.btnOpenImportSession) this.btnOpenImportSession.addEventListener('click', () => this.openModal(this.modalImportSession));
    if (this.btnOpenExtractSession) this.btnOpenExtractSession.addEventListener('click', () => this.openModal(this.modalExtractSession));
    if (this.btnOpenAddProxies) this.btnOpenAddProxies.addEventListener('click', () => this.openModal(this.modalAddProxies));

    // Modal close triggers
    document.querySelectorAll('[data-close]').forEach(btn => {
      btn.addEventListener('click', (e) => {
        const modal = e.target.closest('.modal-backdrop');
        this.closeModal(modal);
      });
    });

    window.addEventListener('click', (e) => {
      if (e.target.classList.contains('modal-backdrop')) {
        this.closeModal(e.target);
      }
    });

    // Form Submissions
    if (this.formCreateJob) this.formCreateJob.addEventListener('submit', (e) => this.handleCreateJob(e));
    if (this.formFilterLeads) this.formFilterLeads.addEventListener('submit', (e) => this.handleFilterLeads(e));
    if (this.formImportSession) this.formImportSession.addEventListener('submit', (e) => this.handleImportSession(e));
    if (this.formExtractSession) this.formExtractSession.addEventListener('submit', (e) => this.handleExtractSession(e));
    if (this.formAddProxies) this.formAddProxies.addEventListener('submit', (e) => this.handleAddProxies(e));

    // FBNumber Settings Form & Token Test
    const formSettings = document.getElementById('form-update-settings');
    if (formSettings) formSettings.addEventListener('submit', (e) => this.handleSaveFBNumberSettings(e));

    const btnToggleToken = document.getElementById('btn-toggle-token-visibility');
    if (btnToggleToken) {
      btnToggleToken.addEventListener('click', () => {
        const input = document.getElementById('cfg-fbnumber-token');
        if (input) {
          input.type = input.type === 'password' ? 'text' : 'password';
        }
      });
    }

    const btnTestToken = document.getElementById('btn-test-fbnumber-token');
    if (btnTestToken) btnTestToken.addEventListener('click', () => this.handleTestFBNumberToken());

    // Toggle sub-options for Profile Enrichment
    const enrichCheckbox = document.getElementById('job-opt-enrich-profiles');
    const subOptionsEnrich = document.getElementById('sub-options-enrich');
    if (enrichCheckbox && subOptionsEnrich) {
      enrichCheckbox.addEventListener('change', () => {
        subOptionsEnrich.style.display = enrichCheckbox.checked ? 'block' : 'none';
      });
    }

    // Export Triggers
    if (this.btnExportCsv) this.btnExportCsv.addEventListener('click', () => this.exportLeads('csv'));
    if (this.btnExportJson) this.btnExportJson.addEventListener('click', () => this.exportLeads('json'));
  }

  handleRouting() {
    const hash = window.location.hash.replace('#', '') || 'dashboard';
    this.switchView(hash);
  }

  switchView(viewName) {
    this.currentView = viewName;
    window.location.hash = viewName;

    // Update nav active state
    this.navItems.forEach(item => {
      if (item.getAttribute('data-view') === viewName) {
        item.classList.add('active');
      } else {
        item.classList.remove('active');
      }
    });

    // Update view sections visibility
    this.viewSections.forEach(section => {
      if (section.id === `view-${viewName}`) {
        section.classList.add('active');
      } else {
        section.classList.remove('active');
      }
    });

    // Update Header Title
    const titles = {
      dashboard: 'Bảng Điều Khiển Tổng Quan',
      jobs: 'Quản Lý & Giám Sát Tiến Trình Crawl',
      leads: 'Khám Phá Khách Hàng & Số Điện Thoại',
      sessions: 'Quản Lý Quần Thể Nick Facebook',
      proxies: 'Quản Lý Quần Thể Proxy',
      settings: 'Cấu Hình API Tra Cứu Số Điện Thoại (FBNumber)'
    };
    if (this.viewHeaderTitle) {
      this.viewHeaderTitle.textContent = titles[viewName] || 'Dashboard';
    }

    this.loadCurrentViewData();
  }

  async fetchApi(endpoint, options = {}) {
    const url = `${this.baseUrl}${endpoint}`;
    const headers = {
      'Content-Type': 'application/json',
      ...(this.apiKey ? { 'X-API-Key': this.apiKey } : {}),
      ...(options.headers || {})
    };

    try {
      const response = await fetch(url, { ...options, headers });
      if (response.status === 401 || response.status === 403) {
        this.setOnlineStatus(false, 'Sai API Key');
        return null;
      }
      if (!response.ok) {
        const errorData = await response.json().catch(() => ({}));
        throw new Error(errorData.message || `Lỗi HTTP ${response.status}`);
      }
      return await response.json();
    } catch (err) {
      console.warn(`Fetch error for ${endpoint}:`, err);
      return null;
    }
  }

  async checkHealth() {
    try {
      const res = await fetch(`${this.baseUrl}/health/live`);
      if (res.ok) {
        this.setOnlineStatus(true, 'Hệ thống Online');
      } else {
        this.setOnlineStatus(false, 'Mất kết nối');
      }
    } catch {
      this.setOnlineStatus(false, 'Offline');
    }
  }

  setOnlineStatus(isOnline, text) {
    if (!this.statusIndicator || !this.statusText) return;
    if (isOnline) {
      this.statusIndicator.classList.add('online');
      this.statusText.textContent = text || 'Online';
    } else {
      this.statusIndicator.classList.remove('online');
      this.statusText.textContent = text || 'Offline';
    }
  }

  loadCurrentViewData() {
    this.checkHealth();
    if (this.currentView === 'dashboard') this.loadDashboardData();
    else if (this.currentView === 'jobs') this.loadJobsData();
    else if (this.currentView === 'leads') this.loadLeadsData();
    else if (this.currentView === 'sessions') this.loadSessionsData();
    else if (this.currentView === 'proxies') this.loadProxiesData();
    else if (this.currentView === 'settings') this.loadSettingsData();
  }

  // ==========================================
  // VIEW 1: DASHBOARD
  // ==========================================
  async loadDashboardData() {
    const stats = await this.fetchApi('/api/v1/stats/overview');
    if (stats) {
      if (this.metricTotalUsers) this.metricTotalUsers.textContent = (stats.total_users || 0).toLocaleString();
      if (this.metricUsersWithPhone) this.metricUsersWithPhone.textContent = (stats.users_with_phone || 0).toLocaleString();
      if (this.metricPhoneRate) {
        const rate = stats.total_users > 0 ? ((stats.users_with_phone / stats.total_users) * 100).toFixed(1) : 0;
        this.metricPhoneRate.textContent = `${rate}%`;
      }
      if (this.metricTotalJobs) this.metricTotalJobs.textContent = (stats.total_jobs || 0).toLocaleString();
      if (this.metricActiveSessions) this.metricActiveSessions.textContent = `${stats.available_sessions || 0}/${stats.total_sessions || 0}`;
      if (this.metricActiveProxies) this.metricActiveProxies.textContent = `${stats.active_proxies || 0}/${stats.total_proxies || 0}`;
    }

    const jobsData = await this.fetchApi('/api/v1/jobs?limit=5');
    if (jobsData && jobsData.items && this.tableRecentJobs) {
      if (jobsData.items.length === 0) {
        this.tableRecentJobs.innerHTML = `<tr><td colspan="5" style="text-align:center; color:var(--text-muted); padding:20px;">Chưa có Job nào.</td></tr>`;
      } else {
        this.tableRecentJobs.innerHTML = jobsData.items.map(job => `
          <tr>
            <td><span style="font-family:monospace; color:var(--accent-secondary);">${job.id.substring(0, 8)}...</span></td>
            <td><strong>${job.action}</strong></td>
            <td>${this.getStatusPill(job.status)}</td>
            <td>${job.completed_targets || 0}/${job.requested_targets || 0} (${job.discovered_users || 0} leads)</td>
            <td>${new Date(job.created_at).toLocaleTimeString('vi-VN')}</td>
          </tr>
        `).join('');
      }
    }

    const usersData = await this.fetchApi('/api/v1/users?limit=5');
    if (usersData && usersData.items && this.tableRecentLeads) {
      if (usersData.items.length === 0) {
        this.tableRecentLeads.innerHTML = `<tr><td colspan="4" style="text-align:center; color:var(--text-muted); padding:20px;">Chưa có khách hàng nào.</td></tr>`;
      } else {
        this.tableRecentLeads.innerHTML = usersData.items.map(u => `
          <tr>
            <td>
              <div style="font-weight:600;">${u.name || u.username || u.facebook_uid || 'Ẩn danh'}</div>
              <div style="font-size:0.75rem; color:var(--text-muted);">UID: ${u.facebook_uid || 'N/A'}</div>
            </td>
            <td>${u.phone_1 ? `<span class="pill success">📞 ${u.phone_1}</span>` : `<span class="pill info">Chưa có SĐT</span>`}</td>
            <td>${u.address || 'N/A'}</td>
            <td>${u.profile_url ? `<a href="${u.profile_url}" target="_blank" class="btn btn-secondary btn-sm">Xem FB ↗</a>` : ''}</td>
          </tr>
        `).join('');
      }
    }
  }

  // ==========================================
  // VIEW 2: JOBS
  // ==========================================
  async loadJobsData() {
    if (!this.tableAllJobs) return;
    const data = await this.fetchApi('/api/v1/jobs?limit=50');
    if (!data || !data.items || data.items.length === 0) {
      this.tableAllJobs.innerHTML = `<tr><td colspan="8" style="text-align:center; color:var(--text-muted); padding:30px;">Chưa có Crawl Job nào. Bấm "+ Tạo Job Mới" để bắt đầu quét.</td></tr>`;
      return;
    }

    this.tableAllJobs.innerHTML = data.items.map(job => {
      const isRunning = (job.status || '').toLowerCase() === 'running';
      const isFailed = (job.status || '').toLowerCase() === 'failed';

      return `
        <tr>
          <td><span style="font-family:monospace; color:var(--accent-secondary); font-weight:600;">${job.id.substring(0, 8)}...</span></td>
          <td><strong>${job.action}</strong></td>
          <td>${this.getStatusPill(job.status)}</td>
          <td>${job.completed_targets || 0}/${job.requested_targets || 0}</td>
          <td>${job.discovered_users || 0}</td>
          <td><span style="color:var(--accent-success); font-weight:600;">${job.persisted_users || 0}</span></td>
          <td>${new Date(job.created_at).toLocaleString('vi-VN')}</td>
          <td>
            <div style="display:flex; gap:6px;">
              <button class="btn btn-secondary btn-sm" onclick="window.dashboardApp.viewJobDetails('${job.id}')">Chi tiết</button>
              ${isRunning ? `<button class="btn btn-danger btn-sm" onclick="window.dashboardApp.cancelJob('${job.id}')">Hủy</button>` : ''}
              ${isFailed ? `<button class="btn btn-primary btn-sm" onclick="window.dashboardApp.retryJob('${job.id}')">Thử lại</button>` : ''}
            </div>
          </td>
        </tr>
      `;
    }).join('');
  }

  async handleCreateJob(e) {
    e.preventDefault();
    const action = document.getElementById('job-action').value;
    const rawTargets = document.getElementById('job-targets').value.trim();

    const targets = rawTargets.split('\n').map(t => t.trim()).filter(t => t.length > 0);
    if (targets.length === 0) {
      alert('Vui lòng nhập ít nhất 1 URL mục tiêu!');
      return;
    }

    const maxUsers = parseInt(document.getElementById('job-opt-max-users')?.value, 10) || 1000;
    const steps = parseInt(document.getElementById('job-opt-steps')?.value, 10) || 20;
    const maxDuration = parseInt(document.getElementById('job-opt-duration')?.value, 10) || 1800;
    const navDelay = parseInt(document.getElementById('job-opt-delay')?.value, 10) || 8;
    const depth = parseInt(document.getElementById('job-opt-depth')?.value, 10) || 1;
    const enrichProfiles = !!document.getElementById('job-opt-enrich-profiles')?.checked;
    const profileLimit = parseInt(document.getElementById('job-opt-profile-limit')?.value, 10) || 20;
    const postSteps = parseInt(document.getElementById('job-opt-post-steps')?.value, 10) || 0;
    const postDuration = parseInt(document.getElementById('job-opt-post-duration')?.value, 10) || 30;

    const checkedFields = Array.from(document.querySelectorAll('input[name="profile_fields"]:checked')).map(cb => cb.value);

    const callFbnumber = document.getElementById('job-opt-call-fbnumber') ? !!document.getElementById('job-opt-call-fbnumber').checked : true;

    const options = {
      steps: Math.min(Math.max(steps, 1), 20),
      max_duration_seconds: Math.min(Math.max(maxDuration, 1), 1800),
      navigation_delay_seconds: Math.min(Math.max(navDelay, 8), 1800),
      call_fbnumber: callFbnumber,
      enrich_profiles: enrichProfiles
    };

    if (action.includes('friends') || action.includes('followers') || action.includes('relationships')) {
      options.depth = depth;
      options.max_users = Math.min(Math.max(maxUsers, 1), 1000);
    }

    if (enrichProfiles) {
      options.profile_limit = Math.min(Math.max(profileLimit, 1), 50);
      if (checkedFields.length > 0) {
        options.profile_fields = checkedFields;
      }
      if (postSteps > 0) {
        options.phone_post_steps = Math.min(postSteps, 20);
        options.phone_post_duration_seconds = Math.min(postDuration, 300);
      }
    }

    const idempotencyKey = 'job_' + Date.now() + '_' + Math.random().toString(36).substring(2, 8);
    const body = {
      mode: 'authenticated',
      action: action,
      targets: targets,
      options: options
    };

    const submitBtn = document.getElementById('btn-submit-create-job');
    if (submitBtn) {
      submitBtn.disabled = true;
      submitBtn.textContent = '⏳ Đang khởi tạo...';
    }

    try {
      const res = await this.fetchApi('/api/v1/jobs', {
        method: 'POST',
        headers: { 'Idempotency-Key': idempotencyKey },
        body: JSON.stringify(body)
      });

      if (res && res.id) {
        this.closeModal(this.modalCreateJob);
        this.formCreateJob.reset();
        this.showToast(`Đã tạo Crawl Job ${res.id.substring(0, 8)} thành công!`);
        this.loadJobsData();
      } else {
        this.showToast('Không thể tạo Job. Vui lòng kiểm tra lại cấu hình!');
      }
    } catch (err) {
      alert('Lỗi tạo Job: ' + err.message);
    } finally {
      if (submitBtn) {
        submitBtn.disabled = false;
        submitBtn.textContent = '🚀 Khởi Chạy Crawl Job Ngay';
      }
    }
  }

  async viewJobDetails(jobId) {
    const job = await this.fetchApi(`/api/v1/jobs/${jobId}`);
    if (!job) return;

    document.getElementById('modal-job-title').textContent = `Job: ${job.id}`;
    document.getElementById('job-detail-status').innerHTML = this.getStatusPill(job.status);
    document.getElementById('job-detail-targets').textContent = `${job.completed_targets || 0}/${job.requested_targets || 0}`;
    document.getElementById('job-detail-discovered').textContent = `${job.discovered_users || 0}`;
    document.getElementById('job-detail-persisted').textContent = `${job.persisted_users || 0}`;

    const eventsContainer = document.getElementById('job-detail-events');
    eventsContainer.textContent = 'Đang nạp events...';
    this.openModal(this.modalJobDetails);

    const eventsData = await this.fetchApi(`/api/v1/jobs/${jobId}/events?limit=50`);
    if (eventsData && eventsData.items && eventsData.items.length > 0) {
      eventsContainer.innerHTML = eventsData.items.map(ev => `
        <div>[${new Date(ev.created_at).toLocaleTimeString('vi-VN')}] [${ev.event_type}] ${ev.safe_message || ''}</div>
      `).join('');
    } else {
      eventsContainer.textContent = 'Chưa có sự kiện nào được ghi nhận cho Job này.';
    }
  }

  async cancelJob(jobId) {
    if (!confirm('Bạn có chắc chắn muốn hủy Job này?')) return;
    const res = await this.fetchApi(`/api/v1/jobs/${jobId}/cancel`, { method: 'POST' });
    if (res) {
      this.showToast('Đã gửi yêu cầu hủy Job!');
      this.loadJobsData();
    }
  }

  async retryJob(jobId) {
    const res = await this.fetchApi(`/api/v1/jobs/${jobId}/retry`, { method: 'POST' });
    if (res) {
      this.showToast('Đã kích hoạt chạy lại Job!');
      this.loadJobsData();
    }
  }

  // ==========================================
  // VIEW 3: LEADS & USERS EXPLORER
  // ==========================================
  async loadLeadsData(filters = {}) {
    if (!this.tableAllLeads) return;
    const params = new URLSearchParams({ limit: 50, ...filters });
    const data = await this.fetchApi(`/api/v1/users?${params.toString()}`);

    if (!data || !data.items || data.items.length === 0) {
      this.tableAllLeads.innerHTML = `<tr><td colspan="8" style="text-align:center; color:var(--text-muted); padding:30px;">Không tìm thấy khách hàng nào phù hợp với bộ lọc.</td></tr>`;
      return;
    }

    this.tableAllLeads.innerHTML = data.items.map(u => {
      const p1 = u.phone_1 ? `<span class="pill success">📞 ${u.phone_1}</span>` : '<span style="color:var(--text-muted);">N/A</span>';
      const p2 = u.phone_2 ? `<span class="pill info">📞 ${u.phone_2}</span>` : '<span style="color:var(--text-muted);">-</span>';

      return `
        <tr>
          <td>
            <div style="font-weight:600;">${u.name || u.username || 'Ẩn danh'}</div>
            <div style="font-size:0.75rem; color:var(--text-muted);">UID: ${u.facebook_uid || 'N/A'}</div>
          </td>
          <td>${u.username || '<span style="color:var(--text-muted);">-</span>'}</td>
          <td>${p1}</td>
          <td>${p2}</td>
          <td>${u.address || '<span style="color:var(--text-muted);">-</span>'}</td>
          <td>${u.gender || '<span style="color:var(--text-muted);">-</span>'}</td>
          <td>${new Date(u.created_at).toLocaleDateString('vi-VN')}</td>
          <td>
            <button class="btn btn-secondary btn-sm" onclick="window.dashboardApp.viewPhoneEvidence(${u.id})">🔍 Bằng chứng</button>
          </td>
        </tr>
      `;
    }).join('');
  }

  handleFilterLeads(e) {
    e.preventDefault();
    const q = document.getElementById('filter-q').value.trim();
    const uid = document.getElementById('filter-uid').value.trim();
    const phone = document.getElementById('filter-phone').value.trim();
    const hasPhone = document.getElementById('filter-has-phone').value;

    const filters = {};
    if (q) filters.q = q;
    if (uid) filters.uid = uid;
    if (phone) filters.phone = phone;
    if (hasPhone) filters.has_phone = hasPhone;

    this.loadLeadsData(filters);
  }

  async viewPhoneEvidence(userId) {
    const data = await this.fetchApi(`/api/v1/users/${userId}/phone-evidence`);
    const tableBody = document.getElementById('table-evidence-body');
    this.openModal(this.modalPhoneEvidence);

    if (!data || !data.items || data.items.length === 0) {
      tableBody.innerHTML = `<tr><td colspan="5" style="text-align:center; color:var(--text-muted); padding:20px;">Không có bản ghi bằng chứng SĐT nào cho user này.</td></tr>`;
      return;
    }

    tableBody.innerHTML = data.items.map(ev => `
      <tr>
        <td><strong>${ev.display_phone || ev.normalized_phone}</strong></td>
        <td><span class="pill info">${ev.origin}</span></td>
        <td>${ev.confidence || 'high'}</td>
        <td>${ev.provider || 'fbnumber'}</td>
        <td>${new Date(ev.first_captured_at).toLocaleString('vi-VN')}</td>
      </tr>
    `).join('');
  }

  exportLeads(format) {
    const url = `${this.baseUrl}/api/v1/export/users?format=${format}&limit=100`;
    window.open(url, '_blank');
    this.showToast(`Đang xuất tệp ${format.toUpperCase()}...`);
  }

  // ==========================================
  // VIEW 4: SESSIONS
  // ==========================================
  async loadSessionsData() {
    if (!this.tableAllSessions) return;
    const data = await this.fetchApi('/api/v1/sessions');
    if (!data || !data.items || data.items.length === 0) {
      this.tableAllSessions.innerHTML = `<tr><td colspan="6" style="text-align:center; color:var(--text-muted); padding:30px;">Chưa có session nào trong SessionPool. Bấm "+ Import Cookie" để thêm nick.</td></tr>`;
      return;
    }

    this.tableAllSessions.innerHTML = data.items.map(s => {
      const statusClass = s.status === 'healthy' ? 'success' : s.status === 'cooldown' ? 'warning' : 'danger';
      return `
        <tr>
          <td><strong style="color:var(--text-primary);">${s.name}</strong></td>
          <td>${s.proxy ? `<span style="font-family:monospace; color:var(--accent-secondary);">${s.proxy}</span>` : '<span style="color:var(--text-muted);">Tự động xoay Proxy</span>'}</td>
          <td><span class="pill ${statusClass}">${s.status}</span></td>
          <td><span style="color:var(--accent-success); font-weight:600;">${s.success_count || 0}</span></td>
          <td><span style="color:var(--accent-danger); font-weight:600;">${s.failure_count || 0}</span></td>
          <td>${s.is_available ? '✅ Sẵn sàng' : '⏳ Tạm khóa/Nghỉ'}</td>
        </tr>
      `;
    }).join('');
  }

  async handleImportSession(e) {
    e.preventDefault();
    const name = document.getElementById('session-name').value.trim() || null;
    const proxy = document.getElementById('session-proxy').value.trim() || null;
    const rawInput = document.getElementById('session-cookies').value.trim();

    if (!rawInput) {
      alert('Vui lòng nhập Cookie hoặc dòng thông tin nick!');
      return;
    }

    // Support single JSON array or multi-line lines
    let itemsToProcess = [rawInput];
    if (!rawInput.startsWith('[')) {
      itemsToProcess = rawInput.split('\n').map(l => l.trim()).filter(l => l.length > 0);
    }

    let successCount = 0;
    for (const item of itemsToProcess) {
      let payloadCookies = item;
      try {
        if (item.startsWith('[') && item.endsWith(']')) {
          payloadCookies = JSON.parse(item);
        }
      } catch (err) {
        payloadCookies = item;
      }

      const res = await this.fetchApi('/api/v1/sessions', {
        method: 'POST',
        body: JSON.stringify({
          name: itemsToProcess.length === 1 ? name : null,
          cookies: payloadCookies,
          proxy: proxy
        })
      });
      if (res && res.name) successCount++;
    }

    if (successCount > 0) {
      this.closeModal(this.modalImportSession);
      this.formImportSession.reset();
      this.showToast(`Đã nạp thành công ${successCount} Session vào Pool!`);
      this.loadSessionsData();
    } else {
      alert('Không thể nạp Session. Vui lòng kiểm tra lại định dạng dữ liệu!');
    }
  }

  async handleExtractSession(e) {
    e.preventDefault();
    const name = document.getElementById('extract-name').value.trim();
    const email = document.getElementById('extract-email').value.trim();
    const password = document.getElementById('extract-password').value.trim();
    const twoFactorCode = document.getElementById('extract-2fa').value.trim() || null;
    const proxy = document.getElementById('extract-proxy').value.trim() || null;
    const submitBtn = document.getElementById('btn-submit-extract-session');

    if (submitBtn) {
      submitBtn.disabled = true;
      submitBtn.textContent = '⏳ Đang Đăng Nhập & Bóc Tách...';
    }

    try {
      const res = await this.fetchApi('/api/v1/sessions/extract', {
        method: 'POST',
        body: JSON.stringify({
          name: name,
          email: email,
          password: password,
          two_factor_code: twoFactorCode,
          proxy: proxy,
          headless: true
        })
      });

      if (res && res.name) {
        this.closeModal(this.modalExtractSession);
        this.formExtractSession.reset();
        this.showToast(`Đã tự động lấy và lưu thành công Session ${res.name}!`);
        this.loadSessionsData();
      } else {
        alert('Không thể bóc tách session. Vui lòng kiểm tra tài khoản/mật khẩu hoặc kết nối mạng.');
      }
    } catch (err) {
      alert('Lỗi đăng nhập: ' + err.message);
    } finally {
      if (submitBtn) {
        submitBtn.disabled = false;
        submitBtn.textContent = '🚀 Bắt Đầu Đăng Nhập & Lấy Cookie';
      }
    }
  }

  // ==========================================
  // VIEW 5: PROXIES
  // ==========================================
  async loadProxiesData() {
    if (!this.tableAllProxies) return;
    const data = await this.fetchApi('/api/v1/proxies');
    if (!data || !data.items || data.items.length === 0) {
      this.tableAllProxies.innerHTML = `<tr><td colspan="8" style="text-align:center; color:var(--text-muted); padding:30px;">Chưa có proxy nào trong ProxyPool. Bấm "+ Thêm Danh Sách Proxy".</td></tr>`;
      return;
    }

    this.tableAllProxies.innerHTML = data.items.map(p => {
      const statusClass = p.status === 'active' ? 'success' : p.status === 'cooldown' ? 'warning' : 'danger';
      return `
        <tr>
          <td><span style="font-family:monospace; color:var(--text-primary);">${p.raw_url}</span></td>
          <td><span class="pill info">${p.scheme.toUpperCase()}</span></td>
          <td><strong>${p.host}</strong></td>
          <td><code>${p.port}</code></td>
          <td><span class="pill ${statusClass}">${p.status}</span></td>
          <td><span style="color:var(--accent-success); font-weight:600;">${p.success_count || 0}</span></td>
          <td><span style="color:var(--accent-danger); font-weight:600;">${p.failure_count || 0}</span></td>
          <td>${p.is_available ? '✅ Hoạt động' : '❌ Ngắt/Cooldown'}</td>
        </tr>
      `;
    }).join('');
  }

  async handleAddProxies(e) {
    e.preventDefault();
    const rawList = document.getElementById('proxies-list').value.trim();
    const proxies = rawList.split('\n').map(p => p.trim()).filter(p => p.length > 0);

    if (proxies.length === 0) {
      alert('Vui lòng nhập ít nhất 1 dòng proxy!');
      return;
    }

    const res = await this.fetchApi('/api/v1/proxies', {
      method: 'POST',
      body: JSON.stringify({ proxies: proxies })
    });

    if (res) {
      this.closeModal(this.modalAddProxies);
      this.formAddProxies.reset();
      this.showToast(`Đã thêm ${proxies.length} proxy vào ProxyPool thành công!`);
      this.loadProxiesData();
    }
  }

  // ==========================================
  // HELPERS
  // ==========================================
  getStatusPill(status) {
    const s = (status || '').toLowerCase();
    if (s === 'completed' || s === 'succeeded' || s === 'healthy' || s === 'active') return `<span class="pill success">Hoàn thành</span>`;
    if (s === 'running') return `<span class="pill info">Đang chạy</span>`;
    if (s === 'queued') return `<span class="pill warning">Đang chờ</span>`;
    if (s === 'failed' || s === 'dead') return `<span class="pill danger">Lỗi</span>`;
    if (s === 'cancelled') return `<span class="pill warning">Đã hủy</span>`;
    if (s === 'cooldown') return `<span class="pill warning">Cooldown</span>`;
    return `<span class="pill info">${status}</span>`;
  }

  // ==========================================
  // VIEW 6: SETTINGS (FBNUMBER API)
  // ==========================================
  async loadSettingsData() {
    const data = await this.fetchApi('/api/v1/settings/fbnumber');
    if (!data) return;

    const urlInput = document.getElementById('cfg-fbnumber-url');
    const tokenInput = document.getElementById('cfg-fbnumber-token');
    const timeoutInput = document.getElementById('cfg-fbnumber-timeout');
    const retriesInput = document.getElementById('cfg-fbnumber-retries');
    const countryInput = document.getElementById('cfg-country-code');

    if (urlInput) urlInput.value = data.api_url || '';
    if (tokenInput) tokenInput.value = data.api_token || '';
    if (timeoutInput) timeoutInput.value = data.timeout_seconds || 15;
    if (retriesInput) retriesInput.value = data.max_retries || 2;
    if (countryInput) countryInput.value = data.default_country_code || '84';
  }

  async handleSaveFBNumberSettings(e) {
    e.preventDefault();
    const btn = document.getElementById('btn-save-settings');
    if (btn) {
      btn.disabled = true;
      btn.textContent = '⏳ Đang lưu...';
    }

    const payload = {
      api_url: document.getElementById('cfg-fbnumber-url').value.trim(),
      api_token: document.getElementById('cfg-fbnumber-token').value.trim(),
      timeout_seconds: parseFloat(document.getElementById('cfg-fbnumber-timeout').value) || 15.0,
      max_retries: parseInt(document.getElementById('cfg-fbnumber-retries').value, 10) || 2,
      default_country_code: document.getElementById('cfg-country-code').value.trim() || '84'
    };

    const res = await this.fetchApi('/api/v1/settings/fbnumber', {
      method: 'POST',
      body: JSON.stringify(payload)
    });

    if (btn) {
      btn.disabled = false;
      btn.textContent = '💾 Lưu Cấu Hình Vào Hệ Thống (.env)';
    }

    if (res) {
      this.showToast('Đã lưu cấu hình FBNumber vào .env thành công!');
    } else {
      alert('Không thể lưu cấu hình. Vui lòng kiểm tra lại!');
    }
  }

  async handleTestFBNumberToken() {
    const btn = document.getElementById('btn-test-fbnumber-token');
    const resultBox = document.getElementById('test-token-result');
    const statusText = document.getElementById('test-token-status');
    const latencyText = document.getElementById('test-token-latency');
    const rawBox = document.getElementById('test-token-raw');

    const url = document.getElementById('cfg-fbnumber-url').value.trim();
    const token = document.getElementById('cfg-fbnumber-token').value.trim();
    const testUid = document.getElementById('cfg-test-uid').value.trim() || '4';

    if (!token) {
      alert('Vui lòng nhập Token trước khi kiểm tra!');
      return;
    }

    if (btn) {
      btn.disabled = true;
      btn.textContent = '⏳ Đang kiểm tra API...';
    }

    const res = await this.fetchApi('/api/v1/settings/fbnumber/test', {
      method: 'POST',
      body: JSON.stringify({
        api_url: url,
        api_token: token,
        test_uid: testUid
      })
    });

    if (btn) {
      btn.disabled = false;
      btn.textContent = '🔍 Gửi Yêu Cầu Test Token Ngay';
    }

    if (resultBox && res) {
      resultBox.style.display = 'block';
      if (res.success) {
        resultBox.style.background = 'rgba(16, 185, 129, 0.15)';
        resultBox.style.border = '1px solid rgba(16, 185, 129, 0.4)';
        statusText.style.color = 'var(--accent-success)';
        statusText.innerHTML = `✅ ${res.message}`;
      } else {
        resultBox.style.background = 'rgba(239, 68, 68, 0.15)';
        resultBox.style.border = '1px solid rgba(239, 68, 68, 0.4)';
        statusText.style.color = 'var(--accent-danger)';
        statusText.innerHTML = `❌ ${res.message}`;
      }
      latencyText.textContent = `⏱️ Độ trễ phản hồi: ${res.latency_ms} ms (HTTP ${res.status_code})`;
      rawBox.textContent = res.raw_response || '(Không có dữ liệu phản hồi)';
    }
  }

  openModal(modal) {
    if (modal) modal.classList.add('open');
  }

  closeModal(modal) {
    if (modal) modal.classList.remove('open');
  }

  saveSettings() {
    if (this.inputApiKey) {
      this.apiKey = this.inputApiKey.value.trim();
      localStorage.setItem('fb_crawl_api_key', this.apiKey);
      this.closeModal(this.modalSettings);
      this.showToast('Đã lưu cấu hình API Key thành công!');
      this.loadCurrentViewData();
    }
  }

  showToast(message) {
    let container = document.querySelector('.toast-container');
    if (!container) {
      container = document.createElement('div');
      container.className = 'toast-container';
      document.body.appendChild(container);
    }

    const toast = document.createElement('div');
    toast.className = 'toast';
    toast.innerHTML = `<span>⚡</span><span>${message}</span>`;
    container.appendChild(toast);

    setTimeout(() => {
      toast.style.opacity = '0';
      toast.style.transform = 'translateY(10px)';
      toast.style.transition = 'all 0.3s ease';
      setTimeout(() => toast.remove(), 300);
    }, 3000);
  }

  startAutoRefresh() {
    this.refreshInterval = setInterval(() => {
      this.loadCurrentViewData();
    }, 60000);
  }
}

// Bootstrap on DOM Ready
document.addEventListener('DOMContentLoaded', () => {
  window.dashboardApp = new DashboardApp();
});
