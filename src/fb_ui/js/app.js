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

    // Pagination States
    this.jobsPagination = {
      limit: 50,
      pageIndex: 0,
      cursorHistory: [null],
      nextCursor: null,
      hasMore: false,
    };

    this.leadsPagination = {
      limit: 50,
      pageIndex: 0,
      cursorHistory: [null],
      nextCursor: null,
      hasMore: false,
      filters: {},
    };

    this.sessionsPagination = {
      limit: 25,
      pageIndex: 0,
      allItems: [],
    };

    this.proxiesPagination = {
      limit: 25,
      pageIndex: 0,
      allItems: [],
    };

    // Force all modals to hidden state on init (prevents CSS conflicts)
    document.querySelectorAll('.modal-backdrop').forEach(m => {
      m.style.display = 'none';
      m.style.opacity = '0';
      m.style.pointerEvents = 'none';
      m.classList.remove('open');
    });
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

    // Pagination Listeners: Jobs
    const jobsLimit = document.getElementById('jobs-page-limit');
    if (jobsLimit) {
      jobsLimit.addEventListener('change', (e) => {
        this.jobsPagination.limit = parseInt(e.target.value, 10) || 50;
        this.jobsPagination.pageIndex = 0;
        this.jobsPagination.cursorHistory = [null];
        this.loadJobsData();
      });
    }
    const btnJobsPrev = document.getElementById('btn-jobs-prev');
    if (btnJobsPrev) {
      btnJobsPrev.addEventListener('click', () => {
        if (this.jobsPagination.pageIndex > 0) {
          this.jobsPagination.pageIndex--;
          this.loadJobsData(this.jobsPagination.cursorHistory[this.jobsPagination.pageIndex]);
        }
      });
    }
    const btnJobsNext = document.getElementById('btn-jobs-next');
    if (btnJobsNext) {
      btnJobsNext.addEventListener('click', () => {
        if (this.jobsPagination.nextCursor) {
          this.jobsPagination.pageIndex++;
          if (this.jobsPagination.cursorHistory.length <= this.jobsPagination.pageIndex) {
            this.jobsPagination.cursorHistory.push(this.jobsPagination.nextCursor);
          }
          this.loadJobsData(this.jobsPagination.nextCursor);
        }
      });
    }

    // Pagination Listeners: Leads
    const leadsLimit = document.getElementById('leads-page-limit');
    if (leadsLimit) {
      leadsLimit.addEventListener('change', (e) => {
        this.leadsPagination.limit = parseInt(e.target.value, 10) || 50;
        this.leadsPagination.pageIndex = 0;
        this.leadsPagination.cursorHistory = [null];
        this.loadLeadsData();
      });
    }
    const btnLeadsPrev = document.getElementById('btn-leads-prev');
    if (btnLeadsPrev) {
      btnLeadsPrev.addEventListener('click', () => {
        if (this.leadsPagination.pageIndex > 0) {
          this.leadsPagination.pageIndex--;
          this.loadLeadsData(this.leadsPagination.cursorHistory[this.leadsPagination.pageIndex]);
        }
      });
    }
    const btnLeadsNext = document.getElementById('btn-leads-next');
    if (btnLeadsNext) {
      btnLeadsNext.addEventListener('click', () => {
        if (this.leadsPagination.nextCursor) {
          this.leadsPagination.pageIndex++;
          if (this.leadsPagination.cursorHistory.length <= this.leadsPagination.pageIndex) {
            this.leadsPagination.cursorHistory.push(this.leadsPagination.nextCursor);
          }
          this.loadLeadsData(this.leadsPagination.nextCursor);
        }
      });
    }

    // Pagination Listeners: Sessions
    const sessionsLimit = document.getElementById('sessions-page-limit');
    if (sessionsLimit) {
      sessionsLimit.addEventListener('change', (e) => {
        this.sessionsPagination.limit = parseInt(e.target.value, 10) || 25;
        this.sessionsPagination.pageIndex = 0;
        this.renderSessionsTable();
      });
    }
    const btnSessionsPrev = document.getElementById('btn-sessions-prev');
    if (btnSessionsPrev) {
      btnSessionsPrev.addEventListener('click', () => {
        if (this.sessionsPagination.pageIndex > 0) {
          this.sessionsPagination.pageIndex--;
          this.renderSessionsTable();
        }
      });
    }
    const btnSessionsNext = document.getElementById('btn-sessions-next');
    if (btnSessionsNext) {
      btnSessionsNext.addEventListener('click', () => {
        const maxPages = Math.ceil(this.sessionsPagination.allItems.length / this.sessionsPagination.limit);
        if (this.sessionsPagination.pageIndex < maxPages - 1) {
          this.sessionsPagination.pageIndex++;
          this.renderSessionsTable();
        }
      });
    }

    // Pagination Listeners: Proxies
    const proxiesLimit = document.getElementById('proxies-page-limit');
    if (proxiesLimit) {
      proxiesLimit.addEventListener('change', (e) => {
        this.proxiesPagination.limit = parseInt(e.target.value, 10) || 25;
        this.proxiesPagination.pageIndex = 0;
        this.renderProxiesTable();
      });
    }
    const btnProxiesPrev = document.getElementById('btn-proxies-prev');
    if (btnProxiesPrev) {
      btnProxiesPrev.addEventListener('click', () => {
        if (this.proxiesPagination.pageIndex > 0) {
          this.proxiesPagination.pageIndex--;
          this.renderProxiesTable();
        }
      });
    }
    const btnProxiesNext = document.getElementById('btn-proxies-next');
    if (btnProxiesNext) {
      btnProxiesNext.addEventListener('click', () => {
        const maxPages = Math.ceil(this.proxiesPagination.allItems.length / this.proxiesPagination.limit);
        if (this.proxiesPagination.pageIndex < maxPages - 1) {
          this.proxiesPagination.pageIndex++;
          this.renderProxiesTable();
        }
      });
    }

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

    // Action Type Selector (Dynamic Form Options & Placeholders)
    const jobActionSelect = document.getElementById('job-action');
    if (jobActionSelect) {
      jobActionSelect.addEventListener('change', (e) => this.handleJobActionChange(e.target.value));
      this.handleJobActionChange(jobActionSelect.value || 'members');
    }

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

  handleJobActionChange(action) {
    const targets = document.getElementById('job-targets');
    const targetsHint = document.getElementById('hint-job-targets');
    const groupDepth = document.getElementById('group-opt-depth');
    const rowSafetyParams = document.getElementById('row-safety-params');
    const rowUsersSteps = document.getElementById('row-users-steps');
    const enrichCheckbox = document.getElementById('job-opt-enrich-profiles');
    const subOptionsEnrich = document.getElementById('sub-options-enrich');

    if (!targets) return;

    if (action === 'members') {
      targets.placeholder = 'https://www.facebook.com/groups/782850425639223\nhttps://www.facebook.com/groups/example_group/members';
      if (targetsHint) targetsHint.innerHTML = '💡 <strong>Mục tiêu:</strong> Nhập đường link Nhóm (Group URL) hoặc link trang Thành viên Nhóm.';
      if (groupDepth) groupDepth.style.display = 'none';
      if (rowSafetyParams) rowSafetyParams.style.gridTemplateColumns = '1fr 1fr';
      if (rowUsersSteps) rowUsersSteps.style.display = 'grid';
    } else if (action === 'comments') {
      targets.placeholder = 'https://www.facebook.com/permalink.php?story_fbid=123456789&id=1000000\nhttps://www.facebook.com/page_name/posts/123456789';
      if (targetsHint) targetsHint.innerHTML = '💡 <strong>Mục tiêu:</strong> Nhập đường link bài viết hoặc video / Reels cần quét danh sách bình luận.';
      if (groupDepth) groupDepth.style.display = 'none';
      if (rowSafetyParams) rowSafetyParams.style.gridTemplateColumns = '1fr 1fr';
      if (rowUsersSteps) rowUsersSteps.style.display = 'grid';
    } else if (action === 'reactions') {
      targets.placeholder = 'https://www.facebook.com/permalink.php?story_fbid=123456789&id=1000000\nhttps://www.facebook.com/page_name/posts/123456789';
      if (targetsHint) targetsHint.innerHTML = '💡 <strong>Mục tiêu:</strong> Nhập đường link bài viết cần quét danh sách người thả cảm xúc (Like, Tim, Haha...).';
      if (groupDepth) groupDepth.style.display = 'none';
      if (rowSafetyParams) rowSafetyParams.style.gridTemplateColumns = '1fr 1fr';
      if (rowUsersSteps) rowUsersSteps.style.display = 'grid';
    } else if (action === 'friends' || action === 'followers') {
      targets.placeholder = 'https://www.facebook.com/username\nhttps://www.facebook.com/profile.php?id=100006631544225';
      if (targetsHint) targetsHint.innerHTML = '💡 <strong>Mục tiêu:</strong> Nhập đường link trang cá nhân (Profile URL) để quét danh sách bạn bè / người theo dõi.';
      if (groupDepth) groupDepth.style.display = 'block';
      if (rowSafetyParams) rowSafetyParams.style.gridTemplateColumns = '1fr 1fr 1fr';
      if (rowUsersSteps) rowUsersSteps.style.display = 'grid';
    } else if (action === 'profile') {
      targets.placeholder = 'https://www.facebook.com/username\nhttps://www.facebook.com/profile.php?id=100006631544225';
      if (targetsHint) targetsHint.innerHTML = '💡 <strong>Mục tiêu:</strong> Nhập đường link trang cá nhân (Profile URL) để bóc tách thông tin hồ sơ chi tiết.';
      if (groupDepth) groupDepth.style.display = 'none';
      if (rowSafetyParams) rowSafetyParams.style.gridTemplateColumns = '1fr 1fr';
      if (rowUsersSteps) rowUsersSteps.style.display = 'none';
      if (enrichCheckbox) {
        enrichCheckbox.checked = true;
        if (subOptionsEnrich) subOptionsEnrich.style.display = 'block';
      }
    }
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
        this.tableRecentJobs.innerHTML = `<tr><td colspan="6" style="text-align:center; color:var(--text-muted); padding:20px;">Chưa có Job nào.</td></tr>`;
      } else {
        this.tableRecentJobs.innerHTML = jobsData.items.map(job => `
          <tr>
            <td><span style="font-family:monospace; color:var(--accent-secondary); font-weight:600;">${job.id.substring(0, 8)}...</span></td>
            <td><strong>${job.action}</strong></td>
            <td>${this.getStatusPill(job.status)}</td>
            <td>${job.completed_targets || 0}/${job.requested_targets || 0} (${job.discovered_users || 0} leads)</td>
            <td>${new Date(job.created_at).toLocaleTimeString('vi-VN')}</td>
            <td>
              <button class="btn btn-secondary btn-sm" onclick="window.dashboardApp.viewJobDetails('${job.id}')">Chi tiết</button>
            </td>
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
  async loadJobsData(cursor = null) {
    if (!this.tableAllJobs) return;
    const limit = this.jobsPagination.limit;
    const url = cursor
      ? `/api/v1/jobs?limit=${limit}&cursor=${encodeURIComponent(cursor)}`
      : `/api/v1/jobs?limit=${limit}`;

    const data = await this.fetchApi(url);
    if (!data || !data.items || data.items.length === 0) {
      this.tableAllJobs.innerHTML = `<tr><td colspan="8" style="text-align:center; color:var(--text-muted); padding:30px;">Chưa có Crawl Job nào. Bấm "+ Tạo Job Mới" để bắt đầu quét.</td></tr>`;
      this.updateJobsPaginationUI(0, false);
      return;
    }

    this.jobsPagination.nextCursor = data.next_cursor || null;
    this.jobsPagination.hasMore = !!data.has_more;
    this.updateJobsPaginationUI(data.items.length, data.has_more);

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

  updateJobsPaginationUI(count, hasMore) {
    const pageNum = this.jobsPagination.pageIndex + 1;
    const info = document.getElementById('jobs-pagination-info');
    const btnPrev = document.getElementById('btn-jobs-prev');
    const btnNext = document.getElementById('btn-jobs-next');

    if (info) info.textContent = `Trang ${pageNum} (${count} jobs)`;
    if (btnPrev) btnPrev.disabled = this.jobsPagination.pageIndex === 0;
    if (btnNext) btnNext.disabled = !hasMore;
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

    const rawMaxUsers = parseInt(document.getElementById('job-opt-max-users')?.value, 10);
    const maxUsers = isNaN(rawMaxUsers) ? 1000 : rawMaxUsers;

    const rawSteps = parseInt(document.getElementById('job-opt-steps')?.value, 10);
    const steps = isNaN(rawSteps) ? 20 : rawSteps;

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
      steps: steps <= 0 ? 0 : Math.min(Math.max(steps, 1), 100),
      max_users: maxUsers <= 0 ? 0 : Math.min(Math.max(maxUsers, 1), 100000),
      max_duration_seconds: Math.min(Math.max(maxDuration, 1), 1800),
      navigation_delay_seconds: Math.min(Math.max(navDelay, 8), 1800),
      call_fbnumber: callFbnumber,
      enrich_profiles: enrichProfiles
    };

    if (action.includes('friends') || action.includes('followers') || action.includes('relationships')) {
      options.depth = depth;
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
    if (!jobId) return;
    this.openModal(this.modalJobDetails);

    const titleEl = document.getElementById('modal-job-title');
    const statusEl = document.getElementById('job-detail-status');
    const targetsEl = document.getElementById('job-detail-targets');
    const discoveredEl = document.getElementById('job-detail-discovered');
    const persistedEl = document.getElementById('job-detail-persisted');
    const eventsContainer = document.getElementById('job-detail-events');

    if (titleEl) titleEl.textContent = `Chi Tiết Job: ${jobId.substring(0, 8)}...`;
    if (statusEl) statusEl.innerHTML = `<span class="pill info">Đang tải...</span>`;
    if (targetsEl) targetsEl.textContent = '...';
    if (discoveredEl) discoveredEl.textContent = '...';
    if (persistedEl) persistedEl.textContent = '...';
    if (eventsContainer) eventsContainer.textContent = '⏳ Đang nạp dòng sự kiện thời gian thực...';

    try {
      const [job, eventsData] = await Promise.all([
        this.fetchApi(`/api/v1/jobs/${jobId}`),
        this.fetchApi(`/api/v1/jobs/${jobId}/events?limit=50`)
      ]);

      if (job) {
        if (titleEl) titleEl.textContent = `Job: ${job.id} (${job.action})`;
        if (statusEl) statusEl.innerHTML = this.getStatusPill(job.status);
        if (targetsEl) targetsEl.textContent = `${job.completed_targets || 0}/${job.requested_targets || 0}`;
        if (discoveredEl) discoveredEl.textContent = `${job.discovered_users || 0}`;
        if (persistedEl) persistedEl.textContent = `${job.persisted_users || 0}`;
      } else {
        if (statusEl) statusEl.innerHTML = `<span class="pill danger">Không tìm thấy</span>`;
      }

      if (eventsData && eventsData.items && eventsData.items.length > 0) {
        eventsContainer.innerHTML = eventsData.items.map(ev => `
          <div style="padding: 2px 0; border-bottom: 1px dashed rgba(255,255,255,0.06);">
            <span style="color: var(--accent-secondary);">[${new Date(ev.created_at).toLocaleTimeString('vi-VN')}]</span>
            <strong style="color: var(--accent-primary);">[${ev.event_type}]</strong>
            <span>${ev.safe_message || 'OK'}</span>
          </div>
        `).join('');
      } else {
        eventsContainer.textContent = 'Chưa có sự kiện (events) nào được ghi nhận cho Job này.';
      }
    } catch (err) {
      if (eventsContainer) eventsContainer.textContent = 'Lỗi nạp dữ liệu chi tiết: ' + err.message;
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
  async loadLeadsData(cursor = null) {
    if (!this.tableAllLeads) return;
    const limit = this.leadsPagination.limit;
    const params = new URLSearchParams({ limit, ...this.leadsPagination.filters });
    if (cursor) params.set('cursor', cursor);

    const data = await this.fetchApi(`/api/v1/users?${params.toString()}`);

    if (!data || !data.items || data.items.length === 0) {
      this.tableAllLeads.innerHTML = `<tr><td colspan="8" style="text-align:center; color:var(--text-muted); padding:30px;">Không tìm thấy khách hàng nào phù hợp với bộ lọc.</td></tr>`;
      this.updateLeadsPaginationUI(0, false);
      return;
    }

    this.leadsPagination.nextCursor = data.next_cursor || null;
    this.leadsPagination.hasMore = !!data.has_more;
    this.updateLeadsPaginationUI(data.items.length, data.has_more);

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

  updateLeadsPaginationUI(count, hasMore) {
    const pageNum = this.leadsPagination.pageIndex + 1;
    const info = document.getElementById('leads-pagination-info');
    const btnPrev = document.getElementById('btn-leads-prev');
    const btnNext = document.getElementById('btn-leads-next');

    if (info) info.textContent = `Trang ${pageNum} (${count} khách hàng)`;
    if (btnPrev) btnPrev.disabled = this.leadsPagination.pageIndex === 0;
    if (btnNext) btnNext.disabled = !hasMore;
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

    this.leadsPagination.filters = filters;
    this.leadsPagination.pageIndex = 0;
    this.leadsPagination.cursorHistory = [null];
    this.loadLeadsData();
  }

  async viewPhoneEvidence(userId) {
    if (!userId) return;
    this.openModal(this.modalPhoneEvidence);
    const tableBody = document.getElementById('table-evidence-body');
    const titleEl = document.getElementById('modal-evidence-title');

    if (titleEl) titleEl.textContent = `Lịch Sử & Bằng Chứng SĐT (User #${userId})`;
    if (tableBody) {
      tableBody.innerHTML = `<tr><td colspan="5" style="text-align:center; color:var(--text-muted); padding:20px;">⏳ Đang nạp danh sách bằng chứng số điện thoại...</td></tr>`;
    }

    try {
      const data = await this.fetchApi(`/api/v1/users/${userId}/phone-evidence`);
      if (!data || !data.items || data.items.length === 0) {
        tableBody.innerHTML = `<tr><td colspan="5" style="text-align:center; color:var(--text-muted); padding:20px;">Không có bản ghi bằng chứng SĐT nào cho user này.</td></tr>`;
        return;
      }

      tableBody.innerHTML = data.items.map(ev => `
        <tr>
          <td><strong style="color:var(--accent-success);">${ev.display_phone || ev.normalized_phone}</strong></td>
          <td><span class="pill info">${ev.origin}</span></td>
          <td>${ev.confidence || 'provider'}</td>
          <td>${ev.provider || 'fbnumber'}</td>
          <td>${new Date(ev.first_captured_at).toLocaleString('vi-VN')}</td>
        </tr>
      `).join('');
    } catch (err) {
      if (tableBody) tableBody.innerHTML = `<tr><td colspan="5" style="text-align:center; color:var(--accent-danger); padding:20px;">Lỗi tải bằng chứng: ${err.message}</td></tr>`;
    }
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
    this.sessionsPagination.allItems = (data && data.items) || [];
    this.sessionsPagination.pageIndex = 0;
    this.renderSessionsTable();
  }

  renderSessionsTable() {
    if (!this.tableAllSessions) return;
    const items = this.sessionsPagination.allItems;
    if (items.length === 0) {
      this.tableAllSessions.innerHTML = `<tr><td colspan="7" style="text-align:center; color:var(--text-muted); padding:30px;">Chưa có session nào trong SessionPool. Bấm "+ Import Cookie" để thêm nick.</td></tr>`;
      this.updateSessionsPaginationUI(0, 1, 1);
      return;
    }

    const limit = this.sessionsPagination.limit;
    const pageIndex = this.sessionsPagination.pageIndex;
    const totalPages = Math.ceil(items.length / limit) || 1;
    const paginated = items.slice(pageIndex * limit, (pageIndex + 1) * limit);

    this.updateSessionsPaginationUI(items.length, pageIndex + 1, totalPages);

    this.tableAllSessions.innerHTML = paginated.map(s => {
      const statusClass = s.status === 'healthy' ? 'success' : s.status === 'cooldown' ? 'warning' : 'danger';
      return `
        <tr>
          <td><strong style="color:var(--text-primary);">${s.name}</strong></td>
          <td>${s.proxy ? `<span style="font-family:monospace; color:var(--accent-secondary);">${s.proxy}</span>` : '<span style="color:var(--text-muted);">Tự động xoay Proxy</span>'}</td>
          <td><span class="pill ${statusClass}">${s.status}</span></td>
          <td><span style="color:var(--accent-success); font-weight:600;">${s.success_count || 0}</span></td>
          <td><span style="color:var(--accent-danger); font-weight:600;">${s.failure_count || 0}</span></td>
          <td>${s.is_available ? '✅ Sẵn sàng' : '⏳ Tạm khóa/Nghỉ'}</td>
          <td>
            <button class="btn btn-primary btn-sm" onclick="window.dashboardApp.launchSessionBrowser('${s.name}')" title="Mở trình duyệt đăng nhập sẵn nick này">
              🚀 Vào FB Ngay
            </button>
          </td>
        </tr>
      `;
    }).join('');
  }

  async launchSessionBrowser(sessionName) {
    this.showToast(`⏳ Đang khởi động trình duyệt cho ${sessionName}...`);
    try {
      const res = await this.fetchApi(`/api/v1/sessions/${encodeURIComponent(sessionName)}/launch`, {
        method: 'POST'
      });
      if (res && res.status === 'success') {
        this.showToast(`✅ Đã mở trình duyệt đăng nhập nick ${sessionName}!`);
      } else {
        alert(res?.detail || 'Không thể mở trình duyệt.');
      }
    } catch (err) {
      alert('Lỗi khởi động trình duyệt: ' + err.message);
    }
  }

  updateSessionsPaginationUI(totalCount, currentPage, totalPages) {
    const info = document.getElementById('sessions-pagination-info');
    const btnPrev = document.getElementById('btn-sessions-prev');
    const btnNext = document.getElementById('btn-sessions-next');

    if (info) info.textContent = `Trang ${currentPage} / ${totalPages} (${totalCount} sessions)`;
    if (btnPrev) btnPrev.disabled = currentPage <= 1;
    if (btnNext) btnNext.disabled = currentPage >= totalPages;
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
    this.proxiesPagination.allItems = (data && data.items) || [];
    this.proxiesPagination.pageIndex = 0;
    this.renderProxiesTable();
  }

  renderProxiesTable() {
    if (!this.tableAllProxies) return;
    const items = this.proxiesPagination.allItems;
    if (items.length === 0) {
      this.tableAllProxies.innerHTML = `<tr><td colspan="8" style="text-align:center; color:var(--text-muted); padding:30px;">Chưa có proxy nào trong ProxyPool. Bấm "+ Thêm Danh Sách Proxy".</td></tr>`;
      this.updateProxiesPaginationUI(0, 1, 1);
      return;
    }

    const limit = this.proxiesPagination.limit;
    const pageIndex = this.proxiesPagination.pageIndex;
    const totalPages = Math.ceil(items.length / limit) || 1;
    const paginated = items.slice(pageIndex * limit, (pageIndex + 1) * limit);

    this.updateProxiesPaginationUI(items.length, pageIndex + 1, totalPages);

    this.tableAllProxies.innerHTML = paginated.map(p => {
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

  updateProxiesPaginationUI(totalCount, currentPage, totalPages) {
    const info = document.getElementById('proxies-pagination-info');
    const btnPrev = document.getElementById('btn-proxies-prev');
    const btnNext = document.getElementById('btn-proxies-next');

    if (info) info.textContent = `Trang ${currentPage} / ${totalPages} (${totalCount} proxies)`;
    if (btnPrev) btnPrev.disabled = currentPage <= 1;
    if (btnNext) btnNext.disabled = currentPage >= totalPages;
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
    // Close any other open modals first to prevent stacking
    document.querySelectorAll('.modal-backdrop.open').forEach(m => {
      m.classList.remove('open');
      m.style.display = 'none';
    });
    const el = typeof modal === 'string' ? document.getElementById(modal) : modal;
    if (!el) return;
    el.style.display = 'flex';
    el.style.opacity = '1';
    el.style.pointerEvents = 'auto';
    el.style.zIndex = '9999';
    el.classList.add('open');
  }

  closeModal(modal) {
    const el = typeof modal === 'string' ? document.getElementById(modal) : modal;
    if (!el) return;
    el.classList.remove('open');
    el.style.display = 'none';
    el.style.opacity = '0';
    el.style.pointerEvents = 'none';
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
