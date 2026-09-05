function request(path, options = {}) {
  const app = getApp();
  const apiBase = app.globalData.apiBase;
  if (!apiBase) return Promise.reject(new Error("api_not_configured"));
  const headers = { Accept: "application/json", ...(options.headers || {}) };
  if (options.token) headers.Authorization = `Bearer ${options.token}`;
  return new Promise((resolve, reject) => {
    wx.request({
      url: `${apiBase}${path}`,
      method: options.method || "GET",
      data: options.data,
      header: headers,
      success(response) {
        if (response.statusCode >= 200 && response.statusCode < 300)
          resolve(response.data);
        else reject(new Error(`request_${response.statusCode}`));
      },
      fail: reject,
    });
  });
}

function login() {
  return new Promise((resolve, reject) => {
    wx.login({
      success: ({ code }) =>
        code ? resolve(code) : reject(new Error("login_failed")),
      fail: reject,
    });
  });
}

function idempotencyKey(prefix) {
  return `${prefix}-${Date.now()}-${Math.random().toString(36).slice(2, 12)}`;
}

module.exports = { request, login, idempotencyKey };
