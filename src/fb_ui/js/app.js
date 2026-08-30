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
    this.installationId = localStorage.getItem('lead_finder_installation_id') || crypto.randomUUID();
    localStorage.setItem('lead_finder_installation_id', this.installationId);
    this.productAccount = null;
    this.pendingAdminAction = null;

    this.initElements();
    this.bindEvents();
    this.applyLicenseDurationPreset();
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
    this.tableAdminLicenseKeys = document.getElementById('table-admin-license-keys-body');
    this.tableAdminAccounts = document.getElementById('table-admin-accounts-body');
    this.tableAdminSubscriptions = document.getElementById('table-admin-subscriptions-body');
    this.tableAdminDevices = document.getElementById('table-admin-devices-body');
    this.tableAdminAuditEvents = document.getElementById('table-admin-audit-events-body');

    // Modals
    this.modalSettings = document.getElementById('modal-settings');
    this.modalCreateJob = document.getElementById('modal-create-job');
    this.modalJobDetails = document.getElementById('modal-job-details');
    this.modalPhoneEvidence = document.getElementById('modal-phone-evidence');
    this.modalImportSession = document.getElementById('modal-import-session');
    this.modalExtractSession = document.getElementById('modal-extract-session');
    this.modalAddProxies = document.getElementById('modal-add-proxies');
    this.modalEditUser = document.getElementById('modal-edit-user');
    this.modalEditSession = document.getElementById('modal-edit-session');
    this.modalEditProxy = document.getElementById('modal-edit-proxy');
    this.modalSyncScans = document.getElementById('modal-sync-fbnumber-scans');
    this.modalGeneratedLicense = document.getElementById('modal-generated-license');
    this.modalAdminSubscriptions = document.getElementById('modal-admin-subscriptions');
    this.modalAdminDevices = document.getElementById('modal-admin-devices');
    this.modalAdminReauth = document.getElementById('modal-admin-reauth');

    // Forms
    this.formCreateJob = document.getElementById('form-create-job');
    this.formFilterLeads = document.getElementById('form-filter-leads');
    this.formImportSession = document.getElementById('form-import-session');
    this.formExtractSession = document.getElementById('form-extract-session');
    this.formAddProxies = document.getElementById('form-add-proxies');
    this.formEditUser = document.getElementById('form-edit-user');
    this.formEditSession = document.getElementById('form-edit-session');
    this.formEditProxy = document.getElementById('form-edit-proxy');
    this.formSyncScans = document.getElementById('form-sync-fbnumber-scans');
    this.formProductLogin = document.getElementById('form-product-login');
    this.formCreateLicense = document.getElementById('form-create-license');
    this.formAdminReauth = document.getElementById('form-admin-reauth');

    // Lead Finder product admin
    this.productAdminLoginPanel = document.getElementById('product-admin-login-panel');
    this.productAdminWorkspace = document.getElementById('product-admin-workspace');
    this.productAdminSessionStatus = document.getElementById('product-admin-session-status');
    this.btnProductLogout = document.getElementById('btn-product-logout');

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
    this.btnOpenSyncScans = document.getElementById('btn-open-sync-scans');
    this.btnPreviewSyncScans = document.getElementById('btn-preview-sync-scans');
    this.btnToggleSyncToken = document.getElementById('btn-toggle-sync-token');

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

    this.adminLicensesPagination = { pageIndex: 0, cursorHistory: [null], nextCursor: null };
    this.adminAccountsPagination = { pageIndex: 0, cursorHistory: [null], nextCursor: null };
    this.adminAuditPagination = { pageIndex: 0, cursorHistory: [null], nextCursor: null };

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
    const btnSaveSettings = document.getElementById('btn-save-api-key-settings');
    if (btnSaveSettings) btnSaveSettings.addEventListener('click', () => this.saveSettings());

    if (this.formProductLogin) {
      this.formProductLogin.addEventListener('submit', (event) => this.handleProductLogin(event));
    }
    if (this.btnProductLogout) {
      this.btnProductLogout.addEventListener('click', () => this.handleProductLogout());
    }
    if (this.formCreateLicense) {
      this.formCreateLicense.addEventListener('submit', (event) => this.handleCreateLicense(event));
    }
    if (this.formAdminReauth) {
      this.formAdminReauth.addEventListener('submit', (event) => this.handleAdminReauthentication(event));
    }
    const durationPreset = document.getElementById('license-duration-preset');
    if (durationPreset) {
      durationPreset.addEventListener('change', () => this.applyLicenseDurationPreset());
    }
    const refreshLicenses = document.getElementById('btn-refresh-admin-licenses');
    if (refreshLicenses) refreshLicenses.addEventListener('click', () => this.loadAdminLicenses(true));
    const refreshAccounts = document.getElementById('btn-refresh-admin-accounts');
    if (refreshAccounts) refreshAccounts.addEventListener('click', () => this.loadAdminAccounts(true));
    const refreshAudit = document.getElementById('btn-refresh-admin-audit');
    if (refreshAudit) refreshAudit.addEventListener('click', () => this.loadAdminAuditEvents(true));
    const adminLicensesPrevious = document.getElementById('btn-admin-licenses-previous');
    if (adminLicensesPrevious) adminLicensesPrevious.addEventListener('click', () => this.changeAdminPage('licenses', -1));
    const adminLicensesNext = document.getElementById('btn-admin-licenses-next');
    if (adminLicensesNext) adminLicensesNext.addEventListener('click', () => this.changeAdminPage('licenses', 1));
    const adminAccountsPrevious = document.getElementById('btn-admin-accounts-previous');
    if (adminAccountsPrevious) adminAccountsPrevious.addEventListener('click', () => this.changeAdminPage('accounts', -1));
    const adminAccountsNext = document.getElementById('btn-admin-accounts-next');
    if (adminAccountsNext) adminAccountsNext.addEventListener('click', () => this.changeAdminPage('accounts', 1));
    const adminAuditPrevious = document.getElementById('btn-admin-audit-previous');
    if (adminAuditPrevious) adminAuditPrevious.addEventListener('click', () => this.changeAdminPage('audit', -1));
    const adminAuditNext = document.getElementById('btn-admin-audit-next');
    if (adminAuditNext) adminAuditNext.addEventListener('click', () => this.changeAdminPage('audit', 1));
    const copyGenerated = document.getElementById('btn-copy-generated-license');
    if (copyGenerated) copyGenerated.addEventListener('click', () => this.copyGeneratedLicenseKey());

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
    if (this.formEditUser) this.formEditUser.addEventListener('submit', (e) => this.handleEditUserSubmit(e));
    if (this.formEditSession) this.formEditSession.addEventListener('submit', (e) => this.handleEditSessionSubmit(e));
    if (this.formEditProxy) this.formEditProxy.addEventListener('submit', (e) => this.handleEditProxySubmit(e));

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
          this.jobsPagination.cursorHistory[this.jobsPagination.pageIndex] = this.jobsPagination.nextCursor;
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
          this.leadsPagination.cursorHistory[this.leadsPagination.pageIndex] = this.leadsPagination.nextCursor;
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

    const formWorkerSettings = document.getElementById('form-update-worker-settings');
    if (formWorkerSettings) formWorkerSettings.addEventListener('submit', (e) => this.handleSaveWorkerSettings(e));

    const btnResetCooldown = document.getElementById('btn-reset-cooldown');
    if (btnResetCooldown) btnResetCooldown.addEventListener('click', () => this.handleResetCooldown());

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

    // FBNumber Scans Sync Triggers
    if (this.btnOpenSyncScans) {
      this.btnOpenSyncScans.addEventListener('click', () => this.handleOpenSyncScansModal());
    }
    if (this.formSyncScans) {
      this.formSyncScans.addEventListener('submit', (e) => {
        e.preventDefault();
        this.handleExecuteSyncScans(false);
      });
    }
    if (this.btnPreviewSyncScans) {
      this.btnPreviewSyncScans.addEventListener('click', () => this.handleExecuteSyncScans(true));
    }
    if (this.btnToggleSyncToken) {
      this.btnToggleSyncToken.addEventListener('click', () => {
        const input = document.getElementById('sync-scans-token');
        if (input) input.type = input.type === 'password' ? 'text' : 'password';
      });
    }
  }

  handleJobActionChange(action) {
    const targets = document.getElementById('job-targets');
    const targetsHint = document.getElementById('hint-job-targets');
    const groupDepth = document.getElementById('group-opt-depth');
    const rowSafetyParams = document.getElementById('row-safety-params');
    const rowUsersSteps = document.getElementById('row-users-steps');
    const autoBatchOptions = document.getElementById('group-auto-batch-options');
    const enrichCheckbox = document.getElementById('job-opt-enrich-profiles');
    const subOptionsEnrich = document.getElementById('sub-options-enrich');

    if (!targets) return;
    if (autoBatchOptions) autoBatchOptions.style.display = action === 'members' ? 'block' : 'none';

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
    if (viewName !== 'product-admin') this.clearGeneratedLicenseKey();
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
      settings: 'Cấu Hình API Tra Cứu Số Điện Thoại (FBNumber)',
      'product-admin': 'Quản Trị Lead Finder'
    };
    if (this.viewHeaderTitle) {
      this.viewHeaderTitle.textContent = titles[viewName] || 'Dashboard';
    }

    this.loadCurrentViewData();
  }

  async fetchApi(endpoint, options = {}) {
    const url = `${this.baseUrl}${endpoint}`;
    const { throwOnError = false, ...fetchOptions } = options;
    const headers = {
      'Content-Type': 'application/json',
      ...(this.apiKey ? { 'X-API-Key': this.apiKey } : {}),
      ...(fetchOptions.headers || {})
    };

    try {
      const response = await fetch(url, { ...fetchOptions, headers });
      if (response.status === 401 || response.status === 403) {
        this.setOnlineStatus(false, 'Sai API Key');
        if (throwOnError) throw new Error('Sai API Key hoặc không có quyền truy cập.');
        return null;
      }
      if (!response.ok) {
        const errorData = await response.json().catch(() => ({}));
        throw new Error(errorData.detail || errorData.message || `Lỗi HTTP ${response.status}`);
      }
      return await response.json();
    } catch (err) {
      console.warn(`Fetch error for ${endpoint}:`, err);
      if (throwOnError) throw err;
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

  escapeHtml(value) {
    return String(value ?? '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  escapeAttr(value) {
    return this.escapeHtml(value).replace(/`/g, '&#96;');
  }

  loadCurrentViewData() {
    this.checkHealth();
    if (this.currentView === 'dashboard') this.loadDashboardData();
    else if (this.currentView === 'jobs') this.loadJobsData();
    else if (this.currentView === 'leads') this.loadLeadsData();
    else if (this.currentView === 'sessions') this.loadSessionsData();
    else if (this.currentView === 'proxies') this.loadProxiesData();
    else if (this.currentView === 'settings') this.loadSettingsData();
    else if (this.currentView === 'product-admin') this.loadProductAdminData();
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
            <td><span style="font-family:monospace; color:var(--accent-secondary); font-weight:600;">${this.escapeHtml(job.id.substring(0, 8))}...</span></td>
            <td><strong>${this.escapeHtml(job.action)}</strong></td>
            <td>${this.getStatusPill(this.escapeHtml(job.status))}</td>
            <td>${job.completed_targets || 0}/${job.requested_targets || 0} (${job.discovered_users || 0} leads)</td>
            <td>${new Date(job.created_at).toLocaleTimeString('vi-VN')}</td>
            <td>
              <button class="btn btn-secondary btn-sm" onclick="window.dashboardApp.viewJobDetails('${this.escapeAttr(job.id)}')">Chi tiết</button>
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
        this.tableRecentLeads.innerHTML = usersData.items.map(u => {
          const fbUrl = u.profile_url || (u.facebook_uid ? `https://www.facebook.com/${encodeURIComponent(u.facebook_uid)}` : (u.username ? `https://www.facebook.com/${encodeURIComponent(u.username)}` : ''));
          const displayName = this.escapeHtml(u.name || u.username || (u.facebook_uid ? `UID: ${u.facebook_uid}` : 'Ẩn danh'));
          const nameHtml = fbUrl
            ? `<a href="${this.escapeAttr(fbUrl)}" target="_blank" rel="noopener noreferrer" style="color:var(--accent-primary); text-decoration:none; font-weight:600;" title="Mở trang Facebook">${displayName} ↗</a>`
            : `<span style="font-weight:600;">${displayName}</span>`;
          return `
          <tr>
            <td>
              <div>${nameHtml}</div>
              <div style="font-size:0.75rem; color:var(--text-muted);">UID: ${this.escapeHtml(u.facebook_uid || 'N/A')}</div>
            </td>
            <td>${u.phone_1 ? `<span class="pill success">📞 ${this.escapeHtml(u.phone_1)}</span>` : `<span class="pill info">Chưa có SĐT</span>`}</td>
            <td>${this.escapeHtml(u.address || 'N/A')}</td>
            <td>${fbUrl ? `<a href="${this.escapeAttr(fbUrl)}" target="_blank" rel="noopener noreferrer" class="btn btn-secondary btn-sm">Xem FB</a>` : ''}</td>
          </tr>
        `;
        }).join('');
      }
    }
  }

  // ==========================================
  // VIEW 2: JOBS
  // ==========================================
  async loadJobsData(cursor = null) {
    if (!this.tableAllJobs) return;
    if (cursor === null) {
      this.jobsPagination.pageIndex = 0;
      this.jobsPagination.cursorHistory = [null];
    }
    const limit = this.jobsPagination.limit;
    const url = cursor
      ? `/api/v1/jobs?limit=${limit}&cursor=${encodeURIComponent(cursor)}`
      : `/api/v1/jobs?limit=${limit}`;

    const data = await this.fetchApi(url);
    if (!data || !data.items || data.items.length === 0) {
      this.tableAllJobs.innerHTML = `<tr><td colspan="8" style="text-align:center; color:var(--text-muted); padding:30px;">Chưa có Crawl Job nào. Bấm "+ Tạo Job Mới" để bắt đầu quét.</td></tr>`;
      this.jobsPagination.nextCursor = null;
      this.jobsPagination.hasMore = false;
      this.updateJobsPaginationUI(0, false);
      return;
    }

    this.jobsPagination.nextCursor = data.next_cursor || null;
    this.jobsPagination.hasMore = Boolean(data.next_cursor);
    this.updateJobsPaginationUI(data.items.length, this.jobsPagination.hasMore);

    this.tableAllJobs.innerHTML = data.items.map(job => {
      const isRunning = (job.status || '').toLowerCase() === 'running';
      const isFailed = (job.status || '').toLowerCase() === 'failed';

      return `
        <tr>
          <td><span style="font-family:monospace; color:var(--accent-secondary); font-weight:600;">${this.escapeHtml(job.id.substring(0, 8))}...</span></td>
          <td><strong>${this.escapeHtml(job.action)}</strong></td>
          <td>${this.getStatusPill(this.escapeHtml(job.status))}</td>
          <td>${job.completed_targets || 0}/${job.requested_targets || 0}</td>
          <td>${job.discovered_users || 0}</td>
          <td><span style="color:var(--accent-success); font-weight:600;">${job.persisted_users || 0}</span></td>
          <td>${new Date(job.created_at).toLocaleString('vi-VN')}</td>
          <td>
            <div style="display:flex; gap:6px;">
              <button class="btn btn-secondary btn-sm" onclick="window.dashboardApp.viewJobDetails('${this.escapeAttr(job.id)}')">Chi tiết</button>
              ${isRunning ? `<button class="btn btn-warning btn-sm" onclick="window.dashboardApp.cancelJob('${this.escapeAttr(job.id)}')">Hủy</button>` : ''}
              ${isFailed ? `<button class="btn btn-primary btn-sm" onclick="window.dashboardApp.retryJob('${this.escapeAttr(job.id)}')">Thử lại</button>` : ''}
              <button class="btn btn-danger btn-sm" onclick="window.dashboardApp.deleteJob('${this.escapeAttr(job.id)}', ${isRunning})">Xóa</button>
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
    const maxUsers = isNaN(rawMaxUsers) ? 100 : rawMaxUsers;

    const rawSteps = parseInt(document.getElementById('job-opt-steps')?.value, 10);
    const steps = isNaN(rawSteps) ? 5 : rawSteps;

    const maxDuration = parseInt(document.getElementById('job-opt-duration')?.value, 10) || 300;
    const navDelay = parseInt(document.getElementById('job-opt-delay')?.value, 10) || 12;
    const depth = parseInt(document.getElementById('job-opt-depth')?.value, 10) || 1;
    const enrichProfiles = !!document.getElementById('job-opt-enrich-profiles')?.checked;
    const profileLimit = parseInt(document.getElementById('job-opt-profile-limit')?.value, 10) || 20;
    const postSteps = parseInt(document.getElementById('job-opt-post-steps')?.value, 10) || 0;
    const postDuration = parseInt(document.getElementById('job-opt-post-duration')?.value, 10) || 30;

    const checkedFields = Array.from(document.querySelectorAll('input[name="profile_fields"]:checked')).map(cb => cb.value);

    const callFbnumber = document.getElementById('job-opt-call-fbnumber') ? !!document.getElementById('job-opt-call-fbnumber').checked : true;

    const options = {
      steps: steps <= 0 ? 0 : Math.min(Math.max(steps, 1), 20),
      max_duration_seconds: Math.min(Math.max(maxDuration, 1), 1800),
      navigation_delay_seconds: Math.min(Math.max(navDelay, 8), 1800),
      call_fbnumber: callFbnumber,
      enrich_profiles: enrichProfiles
    };

    if (['members', 'friends', 'followers'].includes(action)) {
      options.max_users = maxUsers <= 0 ? 0 : Math.min(Math.max(maxUsers, 1), 1000);
    }

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
    const autoBatch = action === 'members' && !!document.getElementById('job-opt-auto-batch')?.checked;
    const batchCount = parseInt(document.getElementById('job-opt-batch-count')?.value, 10) || 5;
    const batchBody = {
      group_url: targets[0],
      batch_count: Math.min(Math.max(batchCount, 1), 100),
      batch_size: options.max_users > 0 ? options.max_users : 300,
      batch_duration_seconds: options.max_duration_seconds,
      navigation_delay_seconds: options.navigation_delay_seconds,
      steps: options.steps,
      call_fbnumber: options.call_fbnumber
    };

    const submitBtn = document.getElementById('btn-submit-create-job');
    if (submitBtn) {
      submitBtn.disabled = true;
      submitBtn.textContent = '⏳ Đang khởi tạo...';
    }

    try {
      if (autoBatch && targets.length !== 1) {
        alert('Auto Batch chỉ nhận 1 group URL mỗi lần để tránh trộn checkpoint/progress.');
        return;
      }

      const res = await this.fetchApi(autoBatch ? '/api/v1/jobs/group-batches' : '/api/v1/jobs', {
        method: 'POST',
        headers: { 'Idempotency-Key': idempotencyKey },
        body: JSON.stringify(autoBatch ? batchBody : body),
        throwOnError: true
      });

      if (autoBatch && res && Array.isArray(res.items)) {
        this.closeModal(this.modalCreateJob);
        this.formCreateJob.reset();
        this.showToast(`Đã tạo ${res.items.length} batch job cho group. Worker sẽ chạy lần lượt.`);
        this.loadJobsData();
      } else if (res && res.id) {
        this.closeModal(this.modalCreateJob);
        this.formCreateJob.reset();
        this.showToast(`Đã tạo Crawl Job ${res.id.substring(0, 8)} thành công!`);
        this.loadJobsData();
      } else {
        this.showToast('Không thể tạo Job. Vui lòng kiểm tra lại cấu hình!');
      }
    } catch (err) {
      this.showToast(`Lỗi tạo Job: ${err.message}`, 'error');
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

  async deleteJob(jobId, isRunning = false) {
    const msg = isRunning
      ? `Job này ĐANG CHẠY. Bạn có chắc chắn muốn DỪNG & XÓA Job ${jobId.substring(0, 8)}...?`
      : `Bạn có chắc chắn muốn XÓA Job ${jobId.substring(0, 8)}...? (Dữ liệu khách hàng đã cào vẫn được giữ nguyên)`;
    if (!confirm(msg)) return;
    const res = await this.fetchApi(`/api/v1/jobs/${jobId}`, { method: 'DELETE' });
    if (res) {
      this.showToast('Đã xóa Crawl Job thành công!');
      this.loadJobsData();
    }
  }

  // ==========================================
  // VIEW 3: LEADS & USERS EXPLORER
  // ==========================================
  async loadLeadsData(cursor = null) {
    if (!this.tableAllLeads) return;
    if (cursor === null) {
      this.leadsPagination.pageIndex = 0;
      this.leadsPagination.cursorHistory = [null];
    }
    const limit = this.leadsPagination.limit;
    const params = new URLSearchParams({ limit, ...this.leadsPagination.filters });
    if (cursor) params.set('cursor', cursor);

    const data = await this.fetchApi(`/api/v1/users?${params.toString()}`);

    if (!data || !data.items || data.items.length === 0) {
      this.tableAllLeads.innerHTML = `<tr><td colspan="9" style="text-align:center; color:var(--text-muted); padding:30px;">Không tìm thấy khách hàng nào phù hợp với bộ lọc.</td></tr>`;
      this.leadsPagination.nextCursor = null;
      this.leadsPagination.hasMore = false;
      this.updateLeadsPaginationUI(0, false);
      return;
    }

    this.leadsPagination.nextCursor = data.next_cursor || null;
    this.leadsPagination.hasMore = Boolean(data.next_cursor);
    this.updateLeadsPaginationUI(data.items.length, this.leadsPagination.hasMore);

    this.tableAllLeads.innerHTML = data.items.map(u => {
      const p1 = u.phone_1 ? `<span class="pill success">📞 ${this.escapeHtml(u.phone_1)}</span>` : '<span style="color:var(--text-muted);">N/A</span>';
      const p2 = u.phone_2 ? `<span class="pill info">📞 ${this.escapeHtml(u.phone_2)}</span>` : '<span style="color:var(--text-muted);">-</span>';
      const birthDate = u.birth_date ? `<span style="font-family:monospace;">${this.escapeHtml(u.birth_date)}</span>` : '<span style="color:var(--text-muted);">-</span>';
      const fbUrl = u.profile_url || (u.facebook_uid ? `https://www.facebook.com/${encodeURIComponent(u.facebook_uid)}` : (u.username ? `https://www.facebook.com/${encodeURIComponent(u.username)}` : ''));
      const displayName = this.escapeHtml(u.name || u.username || (u.facebook_uid ? `UID: ${u.facebook_uid}` : 'Ẩn danh'));
      const nameHtml = fbUrl
        ? `<a href="${this.escapeAttr(fbUrl)}" target="_blank" rel="noopener noreferrer" style="color:var(--accent-primary); text-decoration:none; font-weight:600;" title="Mở trang Facebook cá nhân">${displayName} ↗</a>`
        : `<span style="font-weight:600;">${displayName}</span>`;

      return `
        <tr>
          <td>
            <div>${nameHtml}</div>
            <div style="font-size:0.75rem; color:var(--text-muted);">UID: ${this.escapeHtml(u.facebook_uid || 'N/A')}</div>
          </td>
          <td>${u.username ? this.escapeHtml(u.username) : '<span style="color:var(--text-muted);">-</span>'}</td>
          <td>${p1}</td>
          <td>${p2}</td>
          <td>${u.address ? this.escapeHtml(u.address) : '<span style="color:var(--text-muted);">-</span>'}</td>
          <td>${u.gender ? this.escapeHtml(u.gender) : '<span style="color:var(--text-muted);">-</span>'}</td>
          <td>${birthDate}</td>
          <td>${new Date(u.created_at).toLocaleDateString('vi-VN')}</td>
          <td>
            <div style="display:flex; gap:6px;">
              <button class="btn btn-secondary btn-sm" onclick="window.dashboardApp.viewPhoneEvidence(${u.id})">Bằng chứng</button>
              <button class="btn btn-primary btn-sm" onclick="window.dashboardApp.openEditUserModal(${u.id})">Sửa</button>
              <button class="btn btn-danger btn-sm" onclick="window.dashboardApp.deleteUser(${u.id})">Xóa</button>
            </div>
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

  async openEditUserModal(userId) {
    if (!userId) return;
    const user = await this.fetchApi(`/api/v1/users/${userId}`);
    if (!user) {
      this.showToast('Không tìm thấy thông tin khách hàng!');
      return;
    }

    const idInput = document.getElementById('edit-user-id');
    const nameInput = document.getElementById('edit-user-name');
    const usernameInput = document.getElementById('edit-user-username');
    const phone1Input = document.getElementById('edit-user-phone1');
    const phone2Input = document.getElementById('edit-user-phone2');
    const addressInput = document.getElementById('edit-user-address');
    const genderSelect = document.getElementById('edit-user-gender');
    const birthdateInput = document.getElementById('edit-user-birthdate');
    const titleEl = document.getElementById('modal-edit-user-title');

    if (titleEl) titleEl.textContent = `Sửa Khách Hàng #${user.id} (${user.name || user.facebook_uid || 'Ẩn danh'})`;
    if (idInput) idInput.value = user.id;
    if (nameInput) nameInput.value = user.name || '';
    if (usernameInput) usernameInput.value = user.username || '';
    if (phone1Input) phone1Input.value = user.phone_1 || '';
    if (phone2Input) phone2Input.value = user.phone_2 || '';
    if (addressInput) addressInput.value = user.address || '';
    if (genderSelect) genderSelect.value = user.gender || '';
    if (birthdateInput) birthdateInput.value = user.birth_date || '';

    this.openModal(this.modalEditUser);
  }

  async handleEditUserSubmit(e) {
    e.preventDefault();
    const id = document.getElementById('edit-user-id')?.value;
    if (!id) return;

    const payload = {
      name: document.getElementById('edit-user-name')?.value.trim() || null,
      username: document.getElementById('edit-user-username')?.value.trim() || null,
      phone_1: document.getElementById('edit-user-phone1')?.value.trim() || null,
      phone_2: document.getElementById('edit-user-phone2')?.value.trim() || null,
      address: document.getElementById('edit-user-address')?.value.trim() || null,
      gender: document.getElementById('edit-user-gender')?.value || null,
      birth_date: document.getElementById('edit-user-birthdate')?.value.trim() || null,
    };

    const res = await this.fetchApi(`/api/v1/users/${id}`, {
      method: 'PATCH',
      body: JSON.stringify(payload),
    });

    if (res) {
      this.closeModal(this.modalEditUser);
      this.showToast(`Đã cập nhật thông tin khách hàng #${id} thành công!`);
      this.loadLeadsData();
    } else {
      alert('Không thể sửa khách hàng. Kiểm tra dữ liệu nhập hoặc log API.');
    }
  }

  async deleteUser(userId) {
    if (!confirm(`Bạn có chắc chắn muốn XÓA vĩnh viễn khách hàng #${userId} và dữ liệu liên quan?`)) return;
    const res = await this.fetchApi(`/api/v1/users/${userId}`, { method: 'DELETE' });
    if (res) {
      this.showToast(`Đã xóa khách hàng #${userId} thành công!`);
      this.loadLeadsData();
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
      const statusClass = s.status === 'healthy'
        ? 'success'
        : ['unknown', 'cooldown'].includes(s.status)
          ? 'warning'
          : 'danger';
      const sessionName = this.escapeHtml(s.name);
      const sessionAttr = this.escapeAttr(s.name);
      const proxyLabel = s.proxy ? this.escapeHtml(s.proxy) : null;
      const statusLabel = this.escapeHtml(s.status);
      return `
        <tr>
          <td><strong style="color:var(--text-primary);">${sessionName}</strong></td>
          <td>${proxyLabel ? `<span style="font-family:monospace; color:var(--accent-secondary);">${proxyLabel}</span>` : '<span style="color:var(--text-muted);">Tự động xoay Proxy</span>'}</td>
          <td><span class="pill ${statusClass}">${statusLabel}</span></td>
          <td><span style="color:var(--accent-success); font-weight:600;">${s.success_count || 0}</span></td>
          <td><span style="color:var(--accent-danger); font-weight:600;">${s.failure_count || 0}</span></td>
          <td>${s.is_available ? '✅ Sẵn sàng' : '⏳ Tạm khóa/Nghỉ'}</td>
          <td>
            <div style="display:flex; gap:6px;">
              <button class="btn btn-primary btn-sm" onclick="window.dashboardApp.launchSessionBrowser('${sessionAttr}')" title="Mở trình duyệt đăng nhập sẵn nick này">
                Vào FB
              </button>
              <button class="btn btn-info btn-sm" onclick="window.dashboardApp.checkSessionLive('${sessionAttr}')" title="Kiểm tra Cookie còn Live không">
                Kiểm tra
              </button>
              <button class="btn btn-secondary btn-sm" onclick="window.dashboardApp.openEditSessionModal('${sessionAttr}')">
                Sửa
              </button>
              <button class="btn btn-danger btn-sm" onclick="window.dashboardApp.deleteSession('${sessionAttr}')">
                Xóa
              </button>
            </div>
          </td>
        </tr>
      `;
    }).join('');
  }

  async checkSessionLive(sessionName) {
    this.showToast(`Đang kiểm tra trạng thái nick ${sessionName}...`);
    try {
      const res = await this.fetchApi(`/api/v1/sessions/${encodeURIComponent(sessionName)}/check`, {
        method: 'POST'
      });
      if (res && res.is_live) {
        this.showToast(`Nick ${sessionName} còn Live (Hoạt động tốt)!`);
      } else {
        this.showToast(`Nick ${sessionName}: ${res?.message || 'Không thể xác thực'}`);
      }
      this.loadSessionsData();
    } catch (err) {
      alert(`Lỗi kiểm tra nick ${sessionName}: ` + err.message);
    }
  }

  async checkAllSessions() {
    this.showToast('Đang kiểm tra toàn bộ nick trong Session Pool...');
    try {
      const res = await this.fetchApi('/api/v1/sessions/check-all', {
        method: 'POST'
      });
      if (res) {
        this.showToast(`Đã kiểm tra xong ${res.total_checked} nick!`);
        this.loadSessionsData();
      }
    } catch (err) {
      alert('Lỗi kiểm tra danh sách nick: ' + err.message);
    }
  }

  openEditSessionModal(sessionName) {
    const session = this.sessionsPagination.allItems.find(s => s.name === sessionName);
    if (!session) {
      this.showToast('Không tìm thấy thông tin session!');
      return;
    }

    const nameInput = document.getElementById('edit-session-name');
    const displayInput = document.getElementById('edit-session-display-name');
    const proxyInput = document.getElementById('edit-session-proxy');
    const statusSelect = document.getElementById('edit-session-status');
    const titleEl = document.getElementById('modal-edit-session-title');

    if (titleEl) titleEl.textContent = `Chỉnh Sửa Nick: ${session.name}`;
    if (nameInput) nameInput.value = session.name;
    if (displayInput) displayInput.value = session.name;
    if (proxyInput) proxyInput.value = session.proxy || '';
    if (statusSelect) statusSelect.value = session.status || 'unknown';

    this.openModal(this.modalEditSession);
  }

  async handleEditSessionSubmit(e) {
    e.preventDefault();
    const sessionName = document.getElementById('edit-session-name')?.value;
    if (!sessionName) return;

    const proxy = document.getElementById('edit-session-proxy')?.value.trim() || null;
    const status = document.getElementById('edit-session-status')?.value || null;

    const res = await this.fetchApi(`/api/v1/sessions/${encodeURIComponent(sessionName)}`, {
      method: 'PATCH',
      body: JSON.stringify({ proxy, status }),
    });

    if (res) {
      this.closeModal(this.modalEditSession);
      this.showToast(`Đã cập nhật cấu hình nick ${sessionName} thành công!`);
      this.loadSessionsData();
    }
  }

  async deleteSession(sessionName) {
    if (!confirm(`Bạn có chắc chắn muốn XÓA nick '${sessionName}' khỏi Pool và xóa file Cookie?`)) return;
    const res = await this.fetchApi(`/api/v1/sessions/${encodeURIComponent(sessionName)}`, {
      method: 'DELETE',
    });
    if (res) {
      this.showToast(`Đã xóa nick '${sessionName}' thành công!`);
      this.loadSessionsData();
    }
  }

  async launchSessionBrowser(sessionName) {
    this.showToast(`⏳ Đang khởi động trình duyệt cho ${sessionName}...`);
    try {
      const res = await this.fetchApi(`/api/v1/sessions/${encodeURIComponent(sessionName)}/launch-login`, {
        method: 'POST'
      });
      if (res && (res.status === 'accepted' || res.status === 'success')) {
        this.showToast(`✅ Đã mở browser local cho ${sessionName}. Chỉ nhập mật khẩu trong cửa sổ Facebook.`);
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
    const proxy = document.getElementById('extract-proxy').value.trim() || null;
    const submitBtn = document.getElementById('btn-submit-extract-session');

    if (!name) {
      alert('Vui lòng nhập tên session!');
      return;
    }

    if (submitBtn) {
      submitBtn.disabled = true;
      submitBtn.textContent = '⏳ Đang mở browser local...';
    }

    try {
      const res = await this.fetchApi(`/api/v1/sessions/${encodeURIComponent(name)}/launch-login`, {
        method: 'POST',
        body: JSON.stringify({ proxy: proxy }),
      });

      if (res && res.name) {
        this.closeModal(this.modalExtractSession);
        this.formExtractSession.reset();
        this.showToast(`Đã mở browser local cho session ${res.name}. Nhập tài khoản trực tiếp trong cửa sổ Facebook.`);
        this.loadSessionsData();
      } else {
        alert('Không thể mở browser local. Kiểm tra API có đang chạy trên localhost không.');
      }
    } catch (err) {
      alert('Lỗi mở browser local: ' + err.message);
    } finally {
      if (submitBtn) {
        submitBtn.disabled = false;
        submitBtn.textContent = '🚀 Mở Browser Đăng Nhập Local';
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
      const proxyKey = this.escapeAttr(p.display_url);
      const proxyLabel = this.escapeHtml(p.display_url);
      return `
        <tr>
          <td><span style="font-family:monospace; color:var(--text-primary);">${proxyLabel}</span></td>
          <td><span class="pill info">${this.escapeHtml(p.scheme.toUpperCase())}</span></td>
          <td><strong>${this.escapeHtml(p.host)}</strong></td>
          <td><code>${p.port}</code></td>
          <td><span class="pill ${statusClass}">${this.escapeHtml(p.status)}</span></td>
          <td><span style="color:var(--accent-success); font-weight:600;">${p.success_count || 0}</span></td>
          <td><span style="color:var(--accent-danger); font-weight:600;">${p.failure_count || 0}</span></td>
          <td>${p.is_available ? '✅ Hoạt động' : '❌ Ngắt/Cooldown'}</td>
          <td>
            <div style="display:flex; gap:6px;">
              <button class="btn btn-secondary btn-sm" onclick="window.dashboardApp.openEditProxyModal('${encodeURIComponent(proxyKey)}')">
                Sửa
              </button>
              <button class="btn btn-danger btn-sm" onclick="window.dashboardApp.deleteProxy('${encodeURIComponent(proxyKey)}')">
                Xóa
              </button>
            </div>
          </td>
        </tr>
      `;
    }).join('');
  }

  openEditProxyModal(encodedUrl) {
    const rawUrl = decodeURIComponent(encodedUrl);
    const proxy = this.proxiesPagination.allItems.find(p => p.display_url === rawUrl);
    if (!proxy) {
      this.showToast('Không tìm thấy thông tin proxy!');
      return;
    }

    const rawInput = document.getElementById('edit-proxy-raw');
    const urlInput = document.getElementById('edit-proxy-url');
    const statusSelect = document.getElementById('edit-proxy-status');

    if (rawInput) rawInput.value = proxy.display_url;
    if (urlInput) urlInput.value = proxy.display_url;
    if (statusSelect) statusSelect.value = proxy.status || 'active';

    this.openModal(this.modalEditProxy);
  }

  async handleEditProxySubmit(e) {
    e.preventDefault();
    const rawUrl = document.getElementById('edit-proxy-raw')?.value;
    if (!rawUrl) return;

    const newUrl = document.getElementById('edit-proxy-url')?.value.trim() || null;
    const status = document.getElementById('edit-proxy-status')?.value || null;

    const res = await this.fetchApi('/api/v1/proxies', {
      method: 'PATCH',
      body: JSON.stringify({ raw_url: rawUrl, new_url: newUrl, status: status }),
    });

    if (res) {
      this.closeModal(this.modalEditProxy);
      this.showToast('Đã cập nhật cấu hình proxy thành công!');
      this.loadProxiesData();
    }
  }

  async deleteProxy(encodedUrl) {
    const rawUrl = decodeURIComponent(encodedUrl);
    if (!confirm(`Bạn có chắc chắn muốn XÓA proxy '${rawUrl}' khỏi ProxyPool?`)) return;
    const res = await this.fetchApi('/api/v1/proxies', {
      method: 'DELETE',
      body: JSON.stringify({ raw_url: rawUrl }),
    });
    if (res) {
      this.showToast('Đã xóa proxy khỏi Pool thành công!');
      this.loadProxiesData();
    }
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
    if (s === 'unknown') return `<span class="pill warning">Chưa kiểm tra</span>`;
    if (s === 'manual_review') return `<span class="pill danger">Cần xử lý tay</span>`;
    return `<span class="pill info">${this.escapeHtml(status)}</span>`;
  }

  // ==========================================
  // VIEW 6: SETTINGS (FBNUMBER API & WORKER COOLDOWN)
  // ==========================================
  async loadSettingsData() {
    const [fbData, workerData] = await Promise.all([
      this.fetchApi('/api/v1/settings/fbnumber'),
      this.fetchApi('/api/v1/settings/worker')
    ]);

    if (fbData) {
      const data = fbData;
      const urlInput = document.getElementById('cfg-fbnumber-url');
      const tokenInput = document.getElementById('cfg-fbnumber-token');
      const timeoutInput = document.getElementById('cfg-fbnumber-timeout');
      const retriesInput = document.getElementById('cfg-fbnumber-retries');
      const countryInput = document.getElementById('cfg-country-code');

      if (urlInput) urlInput.value = data.api_url || '';
      if (tokenInput) {
        tokenInput.value = data.api_token || '';
        tokenInput.required = false;
        tokenInput.placeholder = data['api_token_configured'] ? 'Đang dùng token từ .env - để trống vẫn giữ token này' : 'Bearer Token...';
      }
      if (timeoutInput) timeoutInput.value = data.timeout_seconds || 15;
      if (retriesInput) retriesInput.value = data.max_retries || 2;
      if (countryInput) countryInput.value = data.default_country_code || '84';
    }

    if (workerData) {
      const cooldownInput = document.getElementById('cfg-worker-cooldown');
      const navDelayInput = document.getElementById('cfg-worker-nav-delay');
      const timeoutInput = document.getElementById('cfg-worker-timeout');
      const rateLimitInput = document.getElementById('cfg-worker-ratelimit-cooldown');
      const badgeEl = document.getElementById('worker-account-status-badge');
      const infoEl = document.getElementById('worker-account-cooldown-info');

      if (cooldownInput) cooldownInput.value = workerData.cooldown_seconds ?? 3600;
      if (navDelayInput) navDelayInput.value = workerData.navigation_delay_seconds ?? 8;
      if (timeoutInput) timeoutInput.value = workerData.job_timeout_seconds ?? 1800;
      if (rateLimitInput) rateLimitInput.value = workerData.rate_limit_cooldown_seconds ?? 21600;

      if (badgeEl) {
        const st = workerData.account_status;
        const pill = st === 'ready'
          ? '<span class="pill success">✅ Sẵn sàng nhận Job</span>'
          : st === 'cooldown'
            ? '<span class="pill warning">⏳ Đang nghỉ dưỡng nick (Cooldown)</span>'
            : `<span class="pill danger">❌ Trạng thái: ${this.escapeHtml(st)}</span>`;
        badgeEl.innerHTML = pill;
      }

      if (infoEl) {
        if (workerData.account_status === 'cooldown' && workerData.cooldown_until) {
          infoEl.textContent = `Nghỉ đến: ${new Date(workerData.cooldown_until).toLocaleTimeString('vi-VN')} (${new Date(workerData.cooldown_until).toLocaleDateString('vi-VN')})`;
        } else if (workerData.account_status === 'ready') {
          infoEl.textContent = 'Tài khoản ở trạng thái sẵn sàng thực hiện Job tiếp theo ngay khi có trong hàng đợi.';
        } else {
          infoEl.textContent = 'Tài khoản cần kiểm tra hoặc đã dừng an toàn.';
        }
      }
    }
  }

  async handleSaveWorkerSettings(e) {
    e.preventDefault();
    const btn = document.getElementById('btn-save-worker-settings');
    if (btn) {
      btn.disabled = true;
      btn.textContent = '⏳ Đang lưu...';
    }

    const payload = {
      cooldown_seconds: parseInt(document.getElementById('cfg-worker-cooldown').value, 10) || 0,
      navigation_delay_seconds: parseInt(document.getElementById('cfg-worker-nav-delay').value, 10) || 8,
      job_timeout_seconds: parseInt(document.getElementById('cfg-worker-timeout').value, 10) || 1800,
      rate_limit_cooldown_seconds: parseInt(document.getElementById('cfg-worker-ratelimit-cooldown').value, 10) || 21600
    };

    const res = await this.fetchApi('/api/v1/settings/worker', {
      method: 'POST',
      body: JSON.stringify(payload)
    });

    if (btn) {
      btn.disabled = false;
      btn.textContent = '💾 Lưu Cấu Hình Worker (.env)';
    }

    if (res) {
      this.showToast('Đã lưu cấu hình Worker Cooldown vào .env thành công!');
      this.loadSettingsData();
    } else {
      alert('Không thể lưu cấu hình Worker. Vui lòng kiểm tra lại!');
    }
  }

  async handleResetCooldown() {
    if (!confirm('Bạn có chắc chắn muốn BỎ QUA thời gian Cooldown và đưa nick về trạng thái SẴN SÀNG ngay lập tức?')) {
      return;
    }

    const btn = document.getElementById('btn-reset-cooldown');
    if (btn) {
      btn.disabled = true;
      btn.textContent = '⏳ Đang mở khóa...';
    }

    const res = await this.fetchApi('/api/v1/settings/reset-cooldown', { method: 'POST' });

    if (btn) {
      btn.disabled = false;
      btn.textContent = '⚡ Mở Khóa / Bỏ Qua Cooldown Ngay';
    }

    if (res && res.status === 'success') {
      this.showToast(res.message || 'Đã mở khóa Cooldown thành công!');
      this.loadSettingsData();
    } else {
      alert('Không thể reset cooldown. Vui lòng kiểm tra API!');
    }
  }

  async handleSaveFBNumberSettings(e) {
    e.preventDefault();
    const btn = document.getElementById('btn-save-fbnumber-settings');
    if (btn) {
      btn.disabled = true;
      btn.textContent = '⏳ Đang lưu...';
    }

    const tokenInput = document.getElementById('cfg-fbnumber-token');
    const tokenValue = tokenInput.value.trim();

    const payload = {
      api_url: document.getElementById('cfg-fbnumber-url').value.trim(),
      api_token: tokenValue || null,
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
        statusText.textContent = `✅ ${res.message}`;
      } else {
        resultBox.style.background = 'rgba(239, 68, 68, 0.15)';
        resultBox.style.border = '1px solid rgba(239, 68, 68, 0.4)';
        statusText.style.color = 'var(--accent-danger)';
        statusText.textContent = `❌ ${res.message}`;
      }
      latencyText.textContent = `⏱️ Độ trễ phản hồi: ${res.latency_ms} ms (HTTP ${res.status_code})`;
      rawBox.textContent = res.raw_response || '(Không có dữ liệu phản hồi)';
    }
  }

  async handleOpenSyncScansModal() {
    const tokenInput = document.getElementById('sync-scans-token');
    const settingsToken = document.getElementById('cfg-fbnumber-token');

    if (tokenInput && (!tokenInput.value || !tokenInput.value.trim())) {
      if (settingsToken && settingsToken.value.trim()) {
        tokenInput.value = settingsToken.value.trim();
      } else {
        // Fetch current settings token
        const cfg = await this.fetchApi('/api/v1/settings/fbnumber');
        if (cfg && cfg.api_token) {
          tokenInput.value = cfg.api_token;
        }
      }
    }

    // Reset results area
    const resultBox = document.getElementById('sync-scans-result-container');
    if (resultBox) resultBox.style.display = 'none';

    this.openModal(this.modalSyncScans);
  }

  async handleExecuteSyncScans(preview = false) {
    const pageNumberInput = document.getElementById('sync-scans-page-number');
    const pageSizeInput = document.getElementById('sync-scans-page-size');
    const filterInput = document.getElementById('sync-scans-filter');
    const tokenInput = document.getElementById('sync-scans-token');

    const btnSubmit = document.getElementById('btn-submit-sync-scans');
    const btnPreview = document.getElementById('btn-preview-sync-scans');
    const resultBox = document.getElementById('sync-scans-result-container');
    const statusBadge = document.getElementById('sync-scans-status-badge');
    const tbody = document.getElementById('table-sync-scans-preview-body');

    const pageNumber = parseInt(pageNumberInput?.value || '1', 10) || 1;
    const pageSize = parseInt(pageSizeInput?.value || '100', 10) || 100;
    const filter = (filterInput?.value || '').trim();
    const token = (tokenInput?.value || '').trim();

    if (btnSubmit) btnSubmit.disabled = true;
    if (btnPreview) btnPreview.disabled = true;

    const originalSubmitText = btnSubmit?.textContent || '🚀 Bắt Đầu Đồng Bộ & Lưu Vào Database';
    const originalPreviewText = btnPreview?.textContent || '🔍 Xem Trước Dữ Liệu (Preview)';

    if (preview && btnPreview) {
      btnPreview.textContent = '⏳ Đang tải xem trước...';
    } else if (btnSubmit) {
      btnSubmit.textContent = '⏳ Đang tải và lưu vào Database...';
    }

    try {
      const res = await this.fetchApi('/api/v1/users/sync-fbnumber-scans', {
        method: 'POST',
        body: JSON.stringify({
          page_number: pageNumber,
          page_size: pageSize,
          filter: filter,
          api_token: token || undefined,
          preview: preview,
        }),
      });

      if (res && res.success) {
        if (resultBox) resultBox.style.display = 'block';
        if (statusBadge) {
          statusBadge.innerHTML = `
            <span class="pill success" style="padding: 6px 12px; font-size: 0.85rem;">
              ✅ ${res.message}
            </span>
            <span style="font-size: 0.8rem; color: var(--text-muted); margin-left: 10px;">
              Tổng hệ thống: <strong>${res.total_count}</strong> | Lấy về: <strong>${res.fetched_count}</strong> ${preview ? '' : `| Đã lưu/cập nhật DB: <strong>${res.imported_count}</strong>`}
            </span>
          `;
        }

        if (tbody) {
          tbody.innerHTML = '';
          if (res.items && res.items.length > 0) {
            res.items.forEach(item => {
              const tr = document.createElement('tr');
              const uidOrName = item.name ? `<strong>${item.name}</strong><br><span style="color: var(--text-muted); font-size: 0.75rem;">${item.uid || item.username || '-'}</span>` : (item.uid || '-');
              const phone1 = item.phone_1 ? `<span class="pill success" style="font-size: 0.75rem;">${item.phone_1}</span>` : '-';
              const phone2 = item.phone_2 ? `<span class="pill info" style="font-size: 0.75rem;">${item.phone_2}</span>` : '-';
              const addr = item.address || '-';
              const gender = item.gender || '-';
              const birthday = item.birthday || '-';
              const scanAt = item.scan_at ? new Date(item.scan_at).toLocaleString('vi-VN') : '-';

              tr.innerHTML = `
                <td>${uidOrName}</td>
                <td>${phone1}</td>
                <td>${phone2}</td>
                <td>${addr}</td>
                <td>${gender}</td>
                <td>${birthday}</td>
                <td>${scanAt}</td>
              `;
              tbody.appendChild(tr);
            });
          } else {
            tbody.innerHTML = '<tr><td colspan="7" style="text-align: center; color: var(--text-muted); padding: 16px;">Không tìm thấy bản ghi nào phù hợp.</td></tr>';
          }
        }

        if (!preview) {
          this.showToast(res.message, 'success');
          // Reload leads and dashboard
          this.loadLeadsData();
          this.loadDashboardData();
        }
      } else {
        if (resultBox) resultBox.style.display = 'block';
        if (statusBadge) {
          statusBadge.innerHTML = `
            <span class="pill danger" style="padding: 6px 12px; font-size: 0.85rem;">
              ❌ Lỗi: ${res?.message || 'Không thể đồng bộ dữ liệu từ FBNumber'}
            </span>
          `;
        }
      }
    } catch (err) {
      if (resultBox) resultBox.style.display = 'block';
      if (statusBadge) {
        statusBadge.innerHTML = `
          <span class="pill danger" style="padding: 6px 12px; font-size: 0.85rem;">
            ❌ Lỗi kết nối: ${err.message || str(err)}
          </span>
        `;
      }
    } finally {
      if (btnSubmit) {
        btnSubmit.disabled = false;
        btnSubmit.textContent = originalSubmitText;
      }
      if (btnPreview) {
        btnPreview.disabled = false;
        btnPreview.textContent = originalPreviewText;
      }
    }
  }

  getCookie(name) {
    const prefix = `${encodeURIComponent(name)}=`;
    const found = document.cookie.split(';').map(item => item.trim()).find(item => item.startsWith(prefix));
    return found ? decodeURIComponent(found.slice(prefix.length)) : '';
  }

  async fetchProductApi(endpoint, options = {}, retry = true, allowReauth = true) {
    const method = (options.method || 'GET').toUpperCase();
    const headers = {
      'Content-Type': 'application/json',
      'X-Installation-ID': this.installationId,
      ...(options.headers || {})
    };
    if (!['GET', 'HEAD', 'OPTIONS'].includes(method)) {
      const csrfToken = this.getCookie('lead_finder_csrf');
      if (csrfToken) headers['X-CSRF-Token'] = csrfToken;
    }
    const response = await fetch(`${this.baseUrl}${endpoint}`, {
      ...options,
      method,
      headers,
      credentials: 'include'
    });
    if (response.status === 401 && retry && !endpoint.startsWith('/api/v1/auth/')) {
      const refreshed = await this.refreshProductSession();
      if (refreshed) return this.fetchProductApi(endpoint, options, false, allowReauth);
    }
    if (!response.ok) {
      const errorData = await response.json().catch(() => ({}));
      const sensitiveAdminAction = endpoint.startsWith('/api/v1/admin/accounts/')
        && ['/suspend', '/sessions', '/devices/'].some(fragment => endpoint.includes(fragment));
      if (
        response.status === 403
        && allowReauth
        && sensitiveAdminAction
        && errorData.detail === 'Recent authentication required.'
      ) {
        return this.promptAdminReauthentication(
          () => this.fetchProductApi(endpoint, options, false, false)
        );
      }
      throw new Error(errorData.message || errorData.detail || `Lỗi HTTP ${response.status}`);
    }
    return response.status === 204 ? null : response.json();
  }

  async refreshProductSession() {
    const csrfToken = this.getCookie('lead_finder_csrf');
    if (!csrfToken) return false;
    try {
      const response = await fetch(`${this.baseUrl}/api/v1/auth/refresh`, {
        method: 'POST',
        credentials: 'include',
        headers: {
          'Content-Type': 'application/json',
          'X-CSRF-Token': csrfToken
        },
        body: JSON.stringify({
          refresh_token: null,
          installation_id: this.installationId,
          transport: 'web'
        })
      });
      return response.ok;
    } catch {
      return false;
    }
  }

  async handleProductLogin(event) {
    event.preventDefault();
    const emailInput = document.getElementById('product-login-email');
    const passwordInput = document.getElementById('product-login-password');
    const submitButton = document.getElementById('btn-product-login');
    if (!emailInput || !passwordInput) return;
    submitButton.disabled = true;
    try {
      await this.fetchProductApi('/api/v1/auth/login', {
        method: 'POST',
        body: JSON.stringify({
          email: emailInput.value.trim(),
          password: passwordInput.value,
          installation_id: this.installationId,
          device_name: 'Lead Finder Admin Dashboard',
          transport: 'web'
        })
      }, false);
      passwordInput.value = '';
      await this.loadProductAdminData();
    } catch (error) {
      this.showToast(`Đăng nhập thất bại: ${error.message}`, 'error');
    } finally {
      passwordInput.value = '';
      submitButton.disabled = false;
    }
  }

  async handleProductLogout() {
    try {
      await this.fetchProductApi('/api/v1/auth/logout', {
        method: 'POST',
        body: JSON.stringify({ refresh_token: null, transport: 'web' })
      }, false);
    } catch {
      // Local UI state still clears when the server session already expired.
    }
    this.productAccount = null;
    this.setProductAdminAuthState();
    this.clearGeneratedLicenseKey();
  }

  setProductAdminAuthState() {
    const isAdmin = this.productAccount?.role === 'admin';
    if (this.productAdminLoginPanel) this.productAdminLoginPanel.hidden = isAdmin;
    if (this.productAdminWorkspace) this.productAdminWorkspace.hidden = !isAdmin;
    if (this.btnProductLogout) this.btnProductLogout.hidden = !this.productAccount;
    if (this.productAdminSessionStatus) {
      this.productAdminSessionStatus.textContent = isAdmin
        ? this.productAccount.email
        : (this.productAccount ? 'Tài khoản không có quyền admin' : 'Chưa đăng nhập');
      this.productAdminSessionStatus.className = `pill ${isAdmin ? 'success' : 'info'}`;
    }
  }

  async loadProductAdminData() {
    try {
      this.productAccount = await this.fetchProductApi('/api/v1/account/me');
    } catch {
      this.productAccount = null;
    }
    this.setProductAdminAuthState();
    if (this.productAccount?.role !== 'admin') return;
    await Promise.all([
      this.loadAdminLicenses(true),
      this.loadAdminAccounts(true),
      this.loadAdminAuditEvents(true)
    ]);
  }

  applyLicenseDurationPreset() {
    const preset = document.getElementById('license-duration-preset');
    const unit = document.getElementById('license-duration-unit');
    const value = document.getElementById('license-duration-value');
    if (!preset || !unit || !value) return;
    const custom = preset.value === 'custom';
    unit.disabled = !custom;
    value.disabled = !custom;
    if (!custom) {
      const [durationUnit, durationValue] = preset.value.split(':');
      unit.value = durationUnit;
      value.value = durationValue;
    }
  }

  async handleCreateLicense(event) {
    event.preventDefault();
    const durationUnit = document.getElementById('license-duration-unit');
    const durationValue = document.getElementById('license-duration-value');
    const monthlyLimit = document.getElementById('monthly-contact-limit');
    const maxDevices = document.getElementById('max-devices');
    const groupCrawl = document.getElementById('allow-group-crawl');
    const commentCrawl = document.getElementById('allow-comment-crawl');
    const submitButton = document.getElementById('btn-create-license');
    submitButton.disabled = true;
    try {
      const created = await this.fetchProductApi('/api/v1/admin/license-keys', {
        method: 'POST',
        body: JSON.stringify({
          duration: { unit: durationUnit.value, value: Number(durationValue.value) },
          monthly_contact_limit: Number(monthlyLimit.value),
          max_devices: Number(maxDevices.value),
          allow_group_crawl: groupCrawl.checked,
          allow_comment_crawl: commentCrawl.checked
        })
      });
      this.showGeneratedLicenseKey(created.key);
      await this.loadAdminLicenses(true);
    } catch (error) {
      this.showToast(`Không thể tạo license: ${error.message}`, 'error');
    } finally {
      submitButton.disabled = false;
    }
  }

  showGeneratedLicenseKey(plaintext) {
    const generatedKeyOutput = document.getElementById('generated-license-key');
    if (!generatedKeyOutput) return;
    generatedKeyOutput.textContent = plaintext;
    this.openModal(this.modalGeneratedLicense);
  }

  clearGeneratedLicenseKey() {
    const generatedKeyOutput = document.getElementById('generated-license-key');
    if (generatedKeyOutput) generatedKeyOutput.textContent = '';
  }

  async copyGeneratedLicenseKey() {
    const value = document.getElementById('generated-license-key')?.textContent || '';
    if (!value) return;
    try {
      await navigator.clipboard.writeText(value);
      this.showToast('Đã sao chép license key.', 'success');
    } catch {
      this.showToast('Không thể sao chép tự động. Hãy chọn và sao chép key.', 'error');
    }
  }

  async loadAdminLicenses(reset = true) {
    if (!this.tableAdminLicenseKeys) return;
    if (reset) this.adminLicensesPagination = { pageIndex: 0, cursorHistory: [null], nextCursor: null };
    try {
      const cursor = this.adminLicensesPagination.cursorHistory[this.adminLicensesPagination.pageIndex];
      const query = cursor ? `&cursor=${encodeURIComponent(cursor)}` : '';
      const data = await this.fetchProductApi(`/api/v1/admin/license-keys?limit=25${query}`);
      this.adminLicensesPagination.nextCursor = data.next_cursor || null;
      this.renderAdminLicenseRows(data.items || []);
      this.updateAdminPagination('licenses');
    } catch (error) {
      this.renderAdminEmptyRow(this.tableAdminLicenseKeys, 6, error.message);
    }
  }

  renderAdminLicenseRows(items) {
    this.tableAdminLicenseKeys.replaceChildren();
    if (!items.length) {
      this.renderAdminEmptyRow(this.tableAdminLicenseKeys, 6, 'Chưa có license key nào.');
      return;
    }
    items.forEach(item => {
      const row = document.createElement('tr');
      this.appendAdminTextCell(row, item.masked_key);
      this.appendAdminTextCell(row, `${item.duration.value} ${item.duration.unit === 'month' ? 'tháng' : 'ngày'}`);
      this.appendAdminTextCell(row, Number(item.monthly_contact_limit).toLocaleString('vi-VN'));
      this.appendAdminTextCell(row, item.max_devices);
      this.appendAdminStatusCell(row, item.status);
      const actions = document.createElement('td');
      if (item.status !== 'revoked') {
        const revokeButton = this.createAdminButton('Thu hồi', 'btn-danger');
        revokeButton.addEventListener('click', () => this.revokeAdminLicense(item));
        actions.appendChild(revokeButton);
      }
      row.appendChild(actions);
      this.tableAdminLicenseKeys.appendChild(row);
    });
  }

  async revokeAdminLicense(item) {
    if (!window.confirm(`Thu hồi license ${item.masked_key}? Tài khoản đang dùng key này sẽ mất quyền gói trả phí.`)) return;
    try {
      await this.fetchProductApi(`/api/v1/admin/license-keys/${item.id}`, { method: 'DELETE' });
      this.showToast('Đã thu hồi license.', 'success');
      await this.loadAdminLicenses(true);
    } catch (error) {
      this.showToast(`Không thể thu hồi license: ${error.message}`, 'error');
    }
  }

  async loadAdminAccounts(reset = true) {
    if (!this.tableAdminAccounts) return;
    if (reset) this.adminAccountsPagination = { pageIndex: 0, cursorHistory: [null], nextCursor: null };
    try {
      const cursor = this.adminAccountsPagination.cursorHistory[this.adminAccountsPagination.pageIndex];
      const query = cursor ? `&cursor=${encodeURIComponent(cursor)}` : '';
      const data = await this.fetchProductApi(`/api/v1/admin/accounts?limit=25${query}`);
      this.adminAccountsPagination.nextCursor = data.next_cursor || null;
      this.renderAdminAccountRows(data.items || []);
      this.updateAdminPagination('accounts');
    } catch (error) {
      this.renderAdminEmptyRow(this.tableAdminAccounts, 6, error.message);
    }
  }

  renderAdminAccountRows(items) {
    this.tableAdminAccounts.replaceChildren();
    if (!items.length) {
      this.renderAdminEmptyRow(this.tableAdminAccounts, 6, 'Chưa có tài khoản nào.');
      return;
    }
    items.forEach(account => {
      const row = document.createElement('tr');
      this.appendAdminTextCell(row, account.email);
      this.appendAdminTextCell(row, account.role);
      this.appendAdminStatusCell(row, account.status);
      this.appendAdminTextCell(row, account.email_verified_at ? 'Đã xác minh' : 'Chưa xác minh');
      this.appendAdminTextCell(row, this.formatAdminDate(account.created_at));
      const actions = document.createElement('td');
      actions.className = 'admin-row-actions';
      const subscriptionsButton = this.createAdminButton('License', 'btn-secondary');
      subscriptionsButton.addEventListener('click', () => this.openAdminSubscriptions(account));
      actions.appendChild(subscriptionsButton);
      const devicesButton = this.createAdminButton('Thiết bị', 'btn-secondary');
      devicesButton.addEventListener('click', () => this.openAdminDevices(account));
      actions.appendChild(devicesButton);
      if (account.role === 'user' && account.status !== 'deleted') {
        const sessionsButton = this.createAdminButton('Đăng xuất hết', 'btn-secondary');
        sessionsButton.addEventListener('click', () => this.revokeAdminSessions(account));
        actions.appendChild(sessionsButton);
      }
      if (account.role === 'user' && !['suspended', 'deleted'].includes(account.status)) {
        const suspendButton = this.createAdminButton('Tạm khóa', 'btn-danger');
        suspendButton.addEventListener('click', () => this.suspendAdminAccount(account));
        actions.appendChild(suspendButton);
      }
      row.appendChild(actions);
      this.tableAdminAccounts.appendChild(row);
    });
  }

  async suspendAdminAccount(account) {
    if (!window.confirm(`Tạm khóa tài khoản ${account.email} và đăng xuất toàn bộ phiên?`)) return;
    try {
      await this.fetchProductApi(`/api/v1/admin/accounts/${account.id}/suspend`, { method: 'POST' });
      this.showToast('Đã tạm khóa tài khoản.', 'success');
      await this.loadAdminAccounts(true);
    } catch (error) {
      this.showToast(`Không thể tạm khóa: ${error.message}`, 'error');
    }
  }

  async revokeAdminSessions(account) {
    if (!window.confirm(`Đăng xuất toàn bộ thiết bị của ${account.email}?`)) return;
    try {
      await this.fetchProductApi(`/api/v1/admin/accounts/${account.id}/sessions`, { method: 'DELETE' });
      this.showToast('Đã thu hồi toàn bộ phiên đăng nhập.', 'success');
    } catch (error) {
      this.showToast(`Không thể thu hồi phiên: ${error.message}`, 'error');
    }
  }

  async openAdminSubscriptions(account) {
    const title = document.getElementById('admin-subscriptions-title');
    if (title) title.textContent = `Lịch sử license · ${account.email}`;
    this.renderAdminEmptyRow(this.tableAdminSubscriptions, 5, 'Đang tải...');
    this.openModal(this.modalAdminSubscriptions);
    try {
      const data = await this.fetchProductApi(`/api/v1/admin/accounts/${account.id}/subscriptions`);
      this.renderAdminSubscriptionRows(account, data.items || []);
    } catch (error) {
      this.renderAdminEmptyRow(this.tableAdminSubscriptions, 5, error.message);
    }
  }

  renderAdminSubscriptionRows(account, items) {
    this.tableAdminSubscriptions.replaceChildren();
    if (!items.length) {
      this.renderAdminEmptyRow(this.tableAdminSubscriptions, 5, 'Tài khoản chưa gắn license.');
      return;
    }
    items.forEach(item => {
      const row = document.createElement('tr');
      this.appendAdminTextCell(row, `${item.duration.value} ${item.duration.unit === 'month' ? 'tháng' : 'ngày'}`);
      this.appendAdminTextCell(row, this.formatAdminDate(item.starts_at));
      this.appendAdminTextCell(row, this.formatAdminDate(item.ends_at));
      this.appendAdminStatusCell(row, item.status);
      const actions = document.createElement('td');
      if (item.status === 'valid' && new Date(item.starts_at) > new Date()) {
        const startButton = this.createAdminButton('Bắt đầu ngay', 'btn-danger');
        startButton.addEventListener('click', () => this.startAdminSubscriptionNow(account, item));
        actions.appendChild(startButton);
      }
      row.appendChild(actions);
      this.tableAdminSubscriptions.appendChild(row);
    });
  }

  async startAdminSubscriptionNow(account, subscription) {
    const warning = 'Thời gian còn lại của gói hiện tại sẽ bị mất. Bắt đầu gói đã chọn ngay bây giờ?';
    if (!window.confirm(warning)) return;
    try {
      await this.fetchProductApi(
        `/api/v1/admin/accounts/${account.id}/subscriptions/${subscription.subscription_id}/start-now`,
        { method: 'POST' }
      );
      this.showToast('Đã bắt đầu gói license mới.', 'success');
      await this.openAdminSubscriptions(account);
    } catch (error) {
      this.showToast(`Không thể bắt đầu gói: ${error.message}`, 'error');
    }
  }

  async changeAdminPage(kind, direction) {
    const state = this.adminPaginationState(kind);
    if (direction > 0) {
      if (!state.nextCursor) return;
      state.cursorHistory[state.pageIndex + 1] = state.nextCursor;
      state.pageIndex += 1;
    } else {
      if (state.pageIndex === 0) return;
      state.pageIndex -= 1;
      state.nextCursor = null;
    }
    if (kind === 'licenses') await this.loadAdminLicenses(false);
    else if (kind === 'accounts') await this.loadAdminAccounts(false);
    else await this.loadAdminAuditEvents(false);
  }

  updateAdminPagination(kind) {
    const state = this.adminPaginationState(kind);
    const prefix = kind === 'licenses'
      ? 'admin-licenses'
      : (kind === 'accounts' ? 'admin-accounts' : 'admin-audit');
    const previous = document.getElementById(`btn-${prefix}-previous`);
    const next = document.getElementById(`btn-${prefix}-next`);
    const status = document.getElementById(`${prefix}-page-status`);
    if (previous) previous.disabled = state.pageIndex === 0;
    if (next) next.disabled = !state.nextCursor;
    if (status) status.textContent = `Trang ${state.pageIndex + 1}`;
  }

  adminPaginationState(kind) {
    if (kind === 'licenses') return this.adminLicensesPagination;
    if (kind === 'accounts') return this.adminAccountsPagination;
    return this.adminAuditPagination;
  }

  async openAdminDevices(account) {
    const title = document.getElementById('admin-devices-title');
    if (title) title.textContent = `Thiết bị · ${account.email}`;
    this.renderAdminEmptyRow(this.tableAdminDevices, 5, 'Đang tải...');
    this.openModal(this.modalAdminDevices);
    try {
      const data = await this.fetchProductApi(`/api/v1/admin/accounts/${account.id}/devices`);
      this.renderAdminDeviceRows(account, data.items || []);
    } catch (error) {
      this.renderAdminEmptyRow(this.tableAdminDevices, 5, error.message);
    }
  }

  renderAdminDeviceRows(account, items) {
    this.tableAdminDevices.replaceChildren();
    if (!items.length) {
      this.renderAdminEmptyRow(this.tableAdminDevices, 5, 'Tài khoản chưa có thiết bị.');
      return;
    }
    items.forEach(device => {
      const row = document.createElement('tr');
      this.appendAdminTextCell(row, device.display_name);
      this.appendAdminTextCell(row, device.installation_id);
      this.appendAdminStatusCell(row, device.status);
      this.appendAdminTextCell(row, this.formatAdminDate(device.last_seen_at));
      const actions = document.createElement('td');
      if (device.status === 'active' && !device.current) {
        const revokeButton = this.createAdminButton('Thu hồi', 'btn-danger');
        revokeButton.addEventListener('click', () => this.revokeAdminDevice(account, device));
        actions.appendChild(revokeButton);
      }
      row.appendChild(actions);
      this.tableAdminDevices.appendChild(row);
    });
  }

  async revokeAdminDevice(account, device) {
    if (!window.confirm(`Thu hồi thiết bị ${device.display_name} của ${account.email}?`)) return;
    try {
      await this.fetchProductApi(
        `/api/v1/admin/accounts/${account.id}/devices/${device.id}`,
        { method: 'DELETE' }
      );
      this.showToast('Đã thu hồi thiết bị và các phiên liên quan.', 'success');
      await this.openAdminDevices(account);
    } catch (error) {
      this.showToast(`Không thể thu hồi thiết bị: ${error.message}`, 'error');
    }
  }

  promptAdminReauthentication(retryAction) {
    if (this.pendingAdminAction) {
      return Promise.reject(new Error('Password reauthentication is already pending.'));
    }
    const passwordInput = document.getElementById('admin-reauth-password');
    if (passwordInput) passwordInput.value = '';
    this.openModal(this.modalAdminReauth);
    if (passwordInput) passwordInput.focus();
    return new Promise((resolve, reject) => {
      this.pendingAdminAction = { retryAction, resolve, reject };
    });
  }

  async handleAdminReauthentication(event) {
    event.preventDefault();
    const passwordInput = document.getElementById('admin-reauth-password');
    const submitButton = document.getElementById('btn-admin-reauth-submit');
    const pending = this.pendingAdminAction;
    if (!passwordInput || !submitButton || !pending) return;
    submitButton.disabled = true;
    try {
      await this.fetchProductApi('/api/v1/auth/reauthenticate', {
        method: 'POST',
        body: JSON.stringify({ password: passwordInput.value })
      }, false);
    } catch (error) {
      this.showToast(`Không thể xác nhận lại: ${error.message}`, 'error');
      passwordInput.value = '';
      submitButton.disabled = false;
      passwordInput.focus();
      return;
    }
    this.pendingAdminAction = null;
    passwordInput.value = '';
    this.closeModal(this.modalAdminReauth);
    try {
      pending.resolve(await pending.retryAction());
    } catch (error) {
      pending.reject(error);
    } finally {
      submitButton.disabled = false;
    }
  }

  async loadAdminAuditEvents(reset = true) {
    if (!this.tableAdminAuditEvents) return;
    if (reset) this.adminAuditPagination = { pageIndex: 0, cursorHistory: [null], nextCursor: null };
    try {
      const cursor = this.adminAuditPagination.cursorHistory[this.adminAuditPagination.pageIndex];
      const query = cursor ? `&cursor=${encodeURIComponent(cursor)}` : '';
      const data = await this.fetchProductApi(`/api/v1/admin/audit-events?limit=50${query}`);
      this.adminAuditPagination.nextCursor = data.next_cursor || null;
      this.renderAdminAuditRows(data.items || []);
      this.updateAdminPagination('audit');
    } catch (error) {
      this.renderAdminEmptyRow(this.tableAdminAuditEvents, 5, error.message);
    }
  }

  renderAdminAuditRows(items) {
    this.tableAdminAuditEvents.replaceChildren();
    if (!items.length) {
      this.renderAdminEmptyRow(this.tableAdminAuditEvents, 5, 'Chưa có sự kiện quản trị.');
      return;
    }
    items.forEach(event => {
      const row = document.createElement('tr');
      this.appendAdminTextCell(row, event.action);
      this.appendAdminTextCell(row, `${event.target_type} · ${event.target_id}`);
      this.appendAdminTextCell(row, event.actor_account_id ?? 'system');
      this.appendAdminTextCell(row, JSON.stringify(event.details || {}));
      this.appendAdminTextCell(row, this.formatAdminDate(event.created_at));
      this.tableAdminAuditEvents.appendChild(row);
    });
  }

  renderAdminEmptyRow(tableBody, colspan, message) {
    if (!tableBody) return;
    tableBody.replaceChildren();
    const row = document.createElement('tr');
    const cell = document.createElement('td');
    cell.colSpan = colspan;
    cell.className = 'admin-empty-cell';
    cell.textContent = message;
    row.appendChild(cell);
    tableBody.appendChild(row);
  }

  appendAdminTextCell(row, value) {
    const cell = document.createElement('td');
    cell.textContent = String(value ?? '—');
    row.appendChild(cell);
    return cell;
  }

  appendAdminStatusCell(row, status) {
    const cell = document.createElement('td');
    const pill = document.createElement('span');
    const positive = ['active', 'available', 'valid'].includes(status);
    const warning = ['pending', 'redeemed'].includes(status);
    pill.className = `pill ${positive ? 'success' : (warning ? 'warning' : 'danger')}`;
    pill.textContent = status;
    cell.appendChild(pill);
    row.appendChild(cell);
  }

  createAdminButton(label, variant) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = `btn ${variant} btn-sm`;
    button.textContent = label;
    return button;
  }

  formatAdminDate(value) {
    if (!value) return '—';
    return new Date(value).toLocaleString('vi-VN');
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
    if (el === this.modalGeneratedLicense) this.clearGeneratedLicenseKey();
    if (el === this.modalAdminReauth) {
      const passwordInput = document.getElementById('admin-reauth-password');
      if (passwordInput) passwordInput.value = '';
      if (this.pendingAdminAction) {
        this.pendingAdminAction.reject(new Error('Password reauthentication was cancelled.'));
        this.pendingAdminAction = null;
      }
    }
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

  showToast(message, type = 'info') {
    let container = document.querySelector('.toast-container');
    if (!container) {
      container = document.createElement('div');
      container.className = 'toast-container';
      document.body.appendChild(container);
    }

    const toast = document.createElement('div');
    toast.className = `toast toast-${type}`;
    const icon = document.createElement('span');
    icon.textContent = type === 'error' ? '⚠️' : '⚡';
    const text = document.createElement('span');
    text.textContent = message;
    toast.appendChild(icon);
    toast.appendChild(text);
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
