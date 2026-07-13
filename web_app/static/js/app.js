/**
 * Whiteboard Animation AI — 前端交互应用
 * 处理导航、表单提交、任务轮询、画廊展示和费用统计。
 */

// ═══════════════════════════════════════════════════════════════════════════
// 导航
// ═══════════════════════════════════════════════════════════════════════════

function navigateTo(sectionId) {
  document.querySelectorAll('.section').forEach(s => s.classList.remove('active'));
  document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));

  const section = document.getElementById(sectionId);
  if (section) section.classList.add('active');

  const nav = document.querySelector(`.nav-item[data-section="${sectionId}"]`);
  if (nav) nav.classList.add('active');

  // 切换分区时刷新数据
  if (sectionId === 'dashboard') refreshDashboard();
  if (sectionId === 'gallery') refreshGallery();
  if (sectionId === 'costs') refreshCosts();
  if (sectionId === 'logs') refreshLogs();
}

document.querySelectorAll('.nav-item').forEach(item => {
  item.addEventListener('click', (e) => {
    e.preventDefault();
    const section = item.getAttribute('data-section');
    navigateTo(section);
  });
});

// ═══════════════════════════════════════════════════════════════════════════
// 提示通知
// ═══════════════════════════════════════════════════════════════════════════

function toast(message, type = 'info') {
  const container = document.querySelector('.toast-container') || (() => {
    const el = document.createElement('div');
    el.className = 'toast-container';
    document.body.appendChild(el);
    return el;
  })();

  const t = document.createElement('div');
  t.className = `toast toast--${type}`;
  t.textContent = message;
  container.appendChild(t);

  setTimeout(() => {
    t.style.opacity = '0';
    t.style.transform = 'translateX(120%)';
    t.style.transition = 'all 0.3s ease';
    setTimeout(() => t.remove(), 300);
  }, 4000);
}

// ═══════════════════════════════════════════════════════════════════════════
// 状态指示器
// ═══════════════════════════════════════════════════════════════════════════

function setStatus(state) {
  const dot = document.getElementById('statusDot');
  const label = document.getElementById('statusLabel');
  dot.className = 'status-dot';
  if (state === 'busy') { dot.classList.add('busy'); label.textContent = '处理中'; }
  else if (state === 'error') { dot.classList.add('error'); label.textContent = '异常'; }
  else { label.textContent = '就绪'; }
}

// ═══════════════════════════════════════════════════════════════════════════
// API 工具
// ═══════════════════════════════════════════════════════════════════════════

const API = {
  async get(path) {
    const res = await fetch(`/api${path}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return res.json();
  },
  async post(path, body) {
    const res = await fetch(`/api${path}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
    return data;
  },
};

// ═══════════════════════════════════════════════════════════════════════════
// 控制台
// ═══════════════════════════════════════════════════════════════════════════

async function refreshDashboard() {
  try {
    const [jobs, outputs, costs] = await Promise.all([
      API.get('/jobs'),
      API.get('/outputs'),
      API.get('/costs'),
    ]);

    // 统计数据
    const totalVideos = outputs.filter(o => o.has_final_video).length;
    const activeJobs = jobs.filter(j => j.status === 'running' || j.status === 'queued').length;
    const completedJobs = jobs.filter(j => j.status === 'completed').length;
    const totalFinished = jobs.filter(j => j.status === 'completed' || j.status === 'failed').length;
    const successRate = totalFinished > 0 ? Math.round((completedJobs / totalFinished) * 100) + '%' : '—';
    const totalCost = (costs.total_cost || 0).toFixed(4);

    document.getElementById('statTotalVideos').textContent = totalVideos;
    document.getElementById('statActiveJobs').textContent = activeJobs;
    document.getElementById('statSuccessRate').textContent = successRate;
    document.getElementById('statTotalCost').textContent = `¥${totalCost}`;

    // 任务列表
    const list = document.getElementById('jobsList');
    if (jobs.length === 0) {
      list.innerHTML = '<div class="empty-state">暂无任务，创建你的第一个项目吧！</div>';
    } else {
      list.innerHTML = jobs.slice(0, 10).map(j => {
        const badgeClass = `badge--${j.status}`;
        return `
          <div class="job-item">
            <div class="job-item-left">
              <div class="job-item-icon" style="background:${_statusColor(j.status)};color:#fff;">
                ${_statusIcon(j.status)}
              </div>
              <div>
                <div class="job-item-title">${_escapeHtml(j.context)}</div>
                <div class="job-item-meta">${j.language} · ${_timeAgo(j.created_at)} · <span class="badge ${badgeClass}">${_statusLabel(j.status)}</span></div>
              </div>
            </div>
          </div>`;
      }).join('');
    }

    // 有活跃任务则设置忙碌状态
    if (activeJobs > 0) {
      setStatus('busy');
    } else {
      setStatus('idle');
    }

  } catch (err) {
    console.error('控制台刷新失败:', err);
  }
}

// ═══════════════════════════════════════════════════════════════════════════
// 新建项目表单
// ═══════════════════════════════════════════════════════════════════════════

const videoProviderSelect = document.getElementById('videoProviderSelect');
const veoDirCard = document.getElementById('veoDirCard');

videoProviderSelect.addEventListener('change', () => {
  veoDirCard.style.display = videoProviderSelect.value ? 'flex' : 'none';
});

document.getElementById('newProjectForm').addEventListener('submit', async (e) => {
  e.preventDefault();

  const context = document.getElementById('topicInput').value.trim();
  if (!context) {
    toast('请输入视频主题。', 'error');
    return;
  }

  const payload = {
    context,
    language: document.getElementById('languageInput').value.trim() || 'chinese',
    research_mode: document.getElementById('researchSelect').value,
    reference_images: document.getElementById('refImagesToggle').checked,
    fast_mode: document.getElementById('fastModeToggle').checked,
    video_provider: videoProviderSelect.value || null,
    veo_direction: document.getElementById('veoDirToggle').checked,
  };

  const btn = document.getElementById('submitBtn');
  const origText = btn.innerHTML;
  btn.disabled = true;
  btn.innerHTML = '⏳ 提交中…';

  try {
    const job = await API.post('/jobs', payload);
    toast('任务已创建，开始处理！', 'success');

    // 显示进度面板
    document.getElementById('activeJobPanel').style.display = 'block';
    document.getElementById('jobProgressBar').style.width = '0%';
    document.getElementById('jobMessage').textContent = '排队等待中…';
    document.getElementById('jobResult').innerHTML = '';

    _pollJob(job.id);
  } catch (err) {
    toast(`任务启动失败：${err.message}`, 'error');
  } finally {
    btn.disabled = false;
    btn.innerHTML = origText;
  }
});

function _pollJob(jobId) {
  setStatus('busy');

  const interval = setInterval(async () => {
    try {
      const job = await API.get(`/jobs/${jobId}`);

      const bar = document.getElementById('jobProgressBar');
      const msg = document.getElementById('jobMessage');
      const badge = document.getElementById('jobStatusBadge');
      const panel = document.getElementById('activeJobPanel');

      panel.style.display = 'block';
      bar.style.width = `${job.progress}%`;
      msg.textContent = job.message;

      // 根据管道阶段映射进度
      if (job.status === 'running') {
        if (job.message.includes('Research')) bar.style.width = '15%';
        else if (job.message.includes('planning')) bar.style.width = '25%';
        else if (job.message.includes('Scene')) bar.style.width = `${25 + Math.min(60, (job.progress || 10))}%`;
      }

      badge.textContent = _statusLabel(job.status);
      badge.className = `badge badge--${job.status}`;

      if (job.status === 'completed') {
        clearInterval(interval);
        setStatus('idle');
        bar.style.width = '100%';
        toast('视频生成完成！', 'success');
        document.getElementById('jobResult').innerHTML = `
          <div style="padding:12px;background:var(--color-bg);border-radius:var(--radius-sm);">
            <strong>输出文件：</strong> <code style="font-size:0.8rem;">${job.result || '就绪'}</code>
          </div>
          <button class="btn btn-outline" style="margin-top:12px;" onclick="navigateTo('gallery')">
            前往画廊查看 →
          </button>
        `;
        refreshDashboard();
      }

      if (job.status === 'failed') {
        clearInterval(interval);
        setStatus('error');
        toast(`任务失败：${job.error || '未知错误'}`, 'error');
        document.getElementById('jobResult').innerHTML = `
          <div style="padding:12px;background:#fef2f2;border-radius:var(--radius-sm);color:var(--color-red);">
            <strong>错误信息：</strong> ${job.error || '未知'}
          </div>
        `;
        refreshDashboard();
      }

    } catch (err) {
      clearInterval(interval);
      console.error('任务轮询错误:', err);
    }
  }, 2000);
}

// ═══════════════════════════════════════════════════════════════════════════
// 画廊
// ═══════════════════════════════════════════════════════════════════════════

async function refreshGallery() {
  try {
    const outputs = await API.get('/outputs');
    const grid = document.getElementById('galleryGrid');

    if (outputs.length === 0) {
      grid.innerHTML = '<div class="empty-state">暂无视频，快去生成你的第一个动画吧！</div>';
      return;
    }

    grid.innerHTML = outputs.map(o => {
      const hasVideo = o.has_final_video;
      const videoUrl = hasVideo ? `/api/outputs/${o.run_id}/${o.video_name}` : null;
      return `
        <div class="gallery-card">
          <div class="gallery-video-wrap">
            ${hasVideo
              ? `<video src="${videoUrl}" controls preload="metadata" poster=""></video>`
              : `<div class="gallery-placeholder">
                   <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><rect x="3" y="3" width="18" height="18" rx="2"/><polyline points="9 3 9 21"/></svg>
                   <div style="margin-top:8px;">无最终视频</div>
                 </div>`
            }
          </div>
          <div class="gallery-card-info">
            <div class="gallery-card-title">${_escapeHtml(o.run_id)}</div>
            <div class="gallery-card-date">${o.created_at} · ${o.scene_count} 个场景</div>
            ${hasVideo ? `
              <div class="gallery-card-actions">
                <a href="${videoUrl}" download class="btn btn-outline" style="padding:6px 14px;font-size:0.78rem;">下载</a>
              </div>` : ''
            }
          </div>
        </div>`;
    }).join('');

  } catch (err) {
    console.error('画廊刷新失败:', err);
  }
}

// ═══════════════════════════════════════════════════════════════════════════
// 费用统计
// ═══════════════════════════════════════════════════════════════════════════

async function refreshCosts() {
  try {
    const data = await API.get('/costs');

    document.getElementById('costTotalRequests').textContent = data.total_requests || 0;
    document.getElementById('costTotalSpend').textContent = `¥${(data.total_cost || 0).toFixed(6)}`;

    // 按供应商拆分
    const byProv = document.getElementById('costByProvider');
    if (data.by_provider && data.by_provider.length > 0) {
      byProv.innerHTML = `
        <table>
          <thead><tr><th>供应商</th><th>请求数</th><th>总费用 (CNY)</th></tr></thead>
          <tbody>
            ${data.by_provider.map(p => `
              <tr>
                <td><strong>${_escapeHtml(p.provider || '未知')}</strong></td>
                <td>${p.cnt}</td>
                <td>¥${(p.total_cost || 0).toFixed(6)}</td>
              </tr>`).join('')}
          </tbody>
        </table>`;
    } else {
      byProv.innerHTML = '<div class="empty-state">暂无费用数据</div>';
    }

    // 最近请求
    const recentDiv = document.getElementById('costRecentTable');
    if (data.recent && data.recent.length > 0) {
      recentDiv.innerHTML = `
        <table>
          <thead><tr><th>时间</th><th>供应商</th><th>模型</th><th>令牌数</th><th>费用</th></tr></thead>
          <tbody>
            ${data.recent.map(r => `
              <tr>
                <td>${r.created_at || '—'}</td>
                <td>${_escapeHtml(r.provider || '—')}</td>
                <td>${r.model || '—'}</td>
                <td>${r.total_tokens || '—'}</td>
                <td>¥${(r.cost || 0).toFixed(6)}</td>
              </tr>`).join('')}
          </tbody>
        </table>`;
    } else {
      recentDiv.innerHTML = '<div class="empty-state">暂无最近请求记录</div>';
    }

  } catch (err) {
    console.error('费用刷新失败:', err);
  }
}

// ═══════════════════════════════════════════════════════════════════════════
// 运行日志
// ═══════════════════════════════════════════════════════════════════════════

let _logCurrentRunId = null;

async function refreshLogs() {
  try {
    // Populate run selector from outputs list
    const outputs = await API.get('/outputs');
    const select = document.getElementById('logRunSelect');

    select.innerHTML = '<option value="">— 选择一次运行 —</option>';
    outputs.forEach(o => {
      const label = `${o.run_id} (${o.created_at || '?'} · ${o.scene_count} 场景${o.has_log ? ' · 有日志' : ''})`;
      select.innerHTML += `<option value="${o.run_id}">${label}</option>`;
    });

    // Auto-select the most recent run with logs
    const latestWithLog = outputs.find(o => o.has_log);
    if (latestWithLog && !_logCurrentRunId) {
      select.value = latestWithLog.run_id;
      _logCurrentRunId = latestWithLog.run_id;
      await _loadLogEntries(latestWithLog.run_id);
    }

  } catch (err) {
    console.error('刷新日志列表失败:', err);
  }
}

async function _loadLogEntries(runId) {
  if (!runId) {
    document.getElementById('logViewer').innerHTML = '<div class="empty-state">选择一次运行以查看日志</div>';
    document.getElementById('logStatTotal').textContent = '—';
    document.getElementById('logStatErrors').textContent = '—';
    document.getElementById('logStatWarnings').textContent = '—';
    document.getElementById('logEntryCount').textContent = '0 条';
    return;
  }

  _logCurrentRunId = runId;

  const level = document.getElementById('logLevelFilter').value;
  const sceneId = document.getElementById('logSceneFilter').value;

  let query = `?limit=1000`;
  if (level) query += `&level=${level}`;
  if (sceneId) query += `&scene_id=${sceneId}`;

  try {
    const data = await API.get(`/logs/${runId}${query}`);
    const entries = data.entries || [];

    // Stats
    document.getElementById('logStatTotal').textContent = data.count || entries.length;
    document.getElementById('logStatErrors').textContent = entries.filter(e => e.level === 'ERROR' || e.level === 'CRITICAL').length;
    document.getElementById('logStatWarnings').textContent = entries.filter(e => e.level === 'WARNING').length;
    document.getElementById('logEntryCount').textContent = `${entries.length} 条`;

    // Render log viewer
    const viewer = document.getElementById('logViewer');
    if (entries.length === 0) {
      viewer.innerHTML = '<div class="empty-state" style="color:#8b949e;padding:32px;">没有匹配的日志条目</div>';
      return;
    }

    viewer.innerHTML = entries.map(e => {
      const ts = e.ts ? e.ts.substring(11, 19) : (e.created_at || '');
      const level = e.level || 'INFO';
      const msg = _escapeHtml(e.msg || e.message || '');

      // Build context badge
      let ctx = '';
      if (e.scene_id) ctx += `Scene${e.scene_id}`;
      if (e.step_tag) ctx += (ctx ? ':' : '') + e.step_tag;

      // Extra data tooltip
      let extraHtml = '';
      if (e.extra) {
        try {
          const extraObj = typeof e.extra === 'string' ? JSON.parse(e.extra) : e.extra;
          const compact = JSON.stringify(extraObj);
          extraHtml = `<span class="log-entry__extra" title="${_escapeHtml(compact)}">${_escapeHtml(compact.substring(0, 80))}${compact.length > 80 ? '…' : ''}</span>`;
        } catch (_) {}
      }
      if (e.extra_json && !extraHtml) {
        try {
          const ex = JSON.parse(e.extra_json);
          if (ex && Object.keys(ex).length > 0) {
            const compact = JSON.stringify(ex);
            extraHtml = `<span class="log-entry__extra" title="${_escapeHtml(compact)}">${_escapeHtml(compact.substring(0, 80))}${compact.length > 80 ? '…' : ''}</span>`;
          }
        } catch (_) {}
      }

      return `
        <div class="log-entry">
          <span class="log-entry__ts">${_escapeHtml(ts)}</span>
          <span class="log-entry__level log-entry__level--${level}">${_escapeHtml(level)}</span>
          ${ctx ? `<span class="log-entry__ctx">${_escapeHtml(ctx)}</span>` : ''}
          <span class="log-entry__msg">${msg}${extraHtml}</span>
        </div>`;
    }).join('');

  } catch (err) {
    document.getElementById('logViewer').innerHTML = `<div class="empty-state" style="color:#f85149;">加载日志失败：${_escapeHtml(err.message)}</div>`;
  }
}

// Log control event handlers
document.getElementById('logRunSelect').addEventListener('change', function () {
  _loadLogEntries(this.value);
});

document.getElementById('logLevelFilter').addEventListener('change', function () {
  if (_logCurrentRunId) _loadLogEntries(_logCurrentRunId);
});

document.getElementById('logSceneFilter').addEventListener('change', function () {
  if (_logCurrentRunId) _loadLogEntries(_logCurrentRunId);
});

document.getElementById('logRefreshBtn').addEventListener('click', function () {
  if (_logCurrentRunId) _loadLogEntries(_logCurrentRunId);
});

// ═══════════════════════════════════════════════════════════════════════════
// 工具函数
// ═══════════════════════════════════════════════════════════════════════════

function _escapeHtml(str) {
  if (!str) return '';
  const div = document.createElement('div');
  div.textContent = str;
  return div.innerHTML;
}

function _timeAgo(iso) {
  if (!iso) return '';
  const diff = Date.now() - new Date(iso).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return '刚刚';
  if (mins < 60) return `${mins} 分钟前`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours} 小时前`;
  return `${Math.floor(hours / 24)} 天前`;
}

function _statusColor(status) {
  const colors = {
    queued: '#9ca3af',
    running: '#3b82f6',
    completed: '#10b981',
    failed: '#ef4444',
  };
  return colors[status] || '#6b7280';
}

function _statusIcon(status) {
  const icons = {
    queued: '⏳',
    running: '⚡',
    completed: '✅',
    failed: '❌',
  };
  return icons[status] || '•';
}

function _statusLabel(status) {
  const labels = {
    queued: '排队中',
    running: '运行中',
    completed: '已完成',
    failed: '失败',
  };
  return labels[status] || status;
}

// ═══════════════════════════════════════════════════════════════════════════
// 初始化
// ═══════════════════════════════════════════════════════════════════════════

document.addEventListener('DOMContentLoaded', () => {
  refreshDashboard();

  // 每 30 秒刷新控制台
  setInterval(() => {
    const dashboard = document.getElementById('dashboard');
    if (dashboard && dashboard.classList.contains('active')) {
      refreshDashboard();
    }
  }, 30000);
});
