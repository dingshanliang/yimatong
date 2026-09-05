const { request, login, idempotencyKey } = require("../../utils/api");
const {
  extractPublicId,
  extractShops,
  productSummary,
} = require("../../utils/member-state");

Page({
  data: {
    loading: true,
    busy: false,
    error: "",
    publicId: "",
    scanToken: "",
    brandName: "品牌专区",
    productName: "",
    productDescription: "",
    membership: null,
    policy: null,
    agreed: false,
    coupons: [],
    notifications: [],
    preference: null,
    shops: [],
  },

  onLoad(options) {
    const publicId = extractPublicId(options);
    this.setData({ publicId });
    if (!publicId) {
      this.setData({
        loading: false,
        error: "请从产品包装码进入品牌会员专区。",
      });
      return;
    }
    this.bootstrap();
  },

  onPullDownRefresh() {
    this.bootstrap().finally(() => wx.stopPullDownRefresh());
  },

  async bootstrap() {
    if (this.data.busy) return;
    this.setData({ loading: true, busy: true, error: "" });
    try {
      const visitorId = wx.getStorageSync("visitor_id") || "";
      const payload = await request(
        `/c/${encodeURIComponent(this.data.publicId)}`,
        {
          headers: visitorId ? { "X-Visitor-ID": visitorId } : {},
        }
      );
      const summary = productSummary(payload);
      const nextVisitor = payload.scan_info && payload.scan_info.visitor_id;
      if (nextVisitor) wx.setStorageSync("visitor_id", nextVisitor);
      this.setData({
        ...summary,
        scanToken: payload.scan_token || "",
        shops: extractShops(payload),
      });
      await this.restoreOrPrepare();
    } catch (error) {
      this.setData({ error: this.errorMessage(error) });
    } finally {
      this.setData({ loading: false, busy: false });
    }
  },

  async restoreOrPrepare() {
    try {
      const jsCode = await login();
      const session = await request(
        "/api/v1/consumers/membership/miniprogram-session",
        {
          method: "POST",
          token: this.data.scanToken,
          data: { js_code: jsCode },
        }
      );
      if (session.status === "recovered") {
        this.setData({
          scanToken: session.scan_token,
          membership: session.membership,
          policy: null,
        });
        await this.loadMemberSurface();
        return;
      }
    } catch (error) {
      if (String(error.message) !== "request_503")
        this.setData({ error: "微信会员身份暂时无法恢复，仍可查看产品信息。" });
    }
    const policy = await request(
      "/api/v1/public/consents/policy?purpose=brand_membership",
      {
        token: this.data.scanToken,
      }
    );
    this.setData({ policy });
  },

  onAgreementChange(event) {
    this.setData({ agreed: event.detail.value.includes("agree") });
  },

  async joinMember() {
    if (!this.data.agreed || this.data.busy) return;
    this.setData({ busy: true, error: "" });
    try {
      const policy = this.data.policy;
      const consent = await request("/api/v1/public/consents", {
        method: "POST",
        token: this.data.scanToken,
        data: {
          purpose: policy.purpose,
          policy_version: policy.policy_version,
          policy_digest: policy.policy_digest,
          idempotency_key: idempotencyKey("mini-consent"),
        },
      });
      const membership = await request("/api/v1/consumers/membership/join", {
        method: "POST",
        token: this.data.scanToken,
        data: {
          consent_id: consent.consent_id,
          idempotency_key: idempotencyKey("mini-join"),
        },
      });
      this.setData({
        membership,
        scanToken: membership.scan_token,
        policy: null,
      });
      const jsCode = await login();
      await request("/api/v1/consumers/membership/miniprogram-bind", {
        method: "POST",
        token: membership.scan_token,
        data: { js_code: jsCode, idempotency_key: idempotencyKey("mini-bind") },
      });
      await this.loadMemberSurface();
    } catch (error) {
      this.setData({ error: "会员关系未能保存，请稍后重试。" });
    } finally {
      this.setData({ busy: false });
    }
  },

  async loadMemberSurface() {
    const token = this.data.scanToken;
    const [coupons, notifications, preference] = await Promise.all([
      request("/api/v1/consumers/membership/coupons", { token }),
      request("/api/v1/consumers/membership/notifications", { token }),
      request("/api/v1/consumers/membership/notification-preferences", {
        token,
      }),
    ]);
    this.setData({
      coupons: Array.isArray(coupons) ? coupons : [],
      notifications: Array.isArray(notifications) ? notifications : [],
      preference,
    });
  },

  async enableMarketingReminder() {
    if (this.data.busy) return;
    const templateId = getApp().globalData.subscriptionTemplateId;
    if (!templateId) {
      this.setData({
        error: "微信提醒模板尚未完成外部配置，消息中心仍会保留服务事实。",
      });
      return;
    }
    this.setData({ busy: true, error: "" });
    try {
      const subscription = await new Promise((resolve, reject) => {
        wx.requestSubscribeMessage({
          tmplIds: [templateId],
          success: resolve,
          fail: reject,
        });
      });
      if (subscription[templateId] !== "accept")
        throw new Error("subscription_declined");
      const policy = await request(
        "/api/v1/public/consents/policy?purpose=lead_capture",
        { token: this.data.scanToken }
      );
      const consent = await request("/api/v1/public/consents", {
        method: "POST",
        token: this.data.scanToken,
        data: {
          purpose: policy.purpose,
          policy_version: policy.policy_version,
          policy_digest: policy.policy_digest,
          idempotency_key: idempotencyKey("mini-marketing-consent"),
        },
      });
      const preference = await request(
        "/api/v1/consumers/membership/notification-preferences/marketing-subscription",
        {
          method: "POST",
          token: this.data.scanToken,
          data: {
            marketing_consent_id: consent.consent_id,
            template_code: "coupon_expiry",
          },
        }
      );
      this.setData({ preference });
    } catch {
      this.setData({ error: "未开启微信提醒；消息中心与会员权益不受影响。" });
    } finally {
      this.setData({ busy: false });
    }
  },

  async disableMarketingReminder() {
    if (this.data.busy) return;
    this.setData({ busy: true, error: "" });
    try {
      const preference = await request(
        "/api/v1/consumers/membership/notification-preferences/marketing-subscription",
        {
          method: "DELETE",
          token: this.data.scanToken,
        }
      );
      this.setData({ preference });
    } catch {
      this.setData({ error: "退订暂时未保存，请稍后重试。" });
    } finally {
      this.setData({ busy: false });
    }
  },

  async openShop(event) {
    const shop = this.data.shops[event.currentTarget.dataset.index];
    if (!shop || !shop.url) return;
    try {
      let handoff = "";
      if (shop.commerce_connection_id && this.data.membership) {
        const result = await request(
          "/api/v1/consumers/membership/commerce-handoffs",
          {
            method: "POST",
            token: this.data.scanToken,
            data: {
              connection_id: shop.commerce_connection_id,
              idempotency_key: idempotencyKey("mini-shop"),
            },
          }
        );
        handoff = result.handoff_token || "";
      }
      const storageKey = idempotencyKey("handoff");
      wx.setStorageSync(storageKey, { url: shop.url, handoff });
      wx.navigateTo({
        url: `/pages/webview/index?key=${encodeURIComponent(storageKey)}`,
      });
    } catch {
      this.setData({ error: "会员身份暂时无法带入商城，请稍后重试。" });
    }
  },

  errorMessage(error) {
    return String(error && error.message) === "api_not_configured"
      ? "小程序服务地址尚未配置，请使用包装码上的 H5 入口。"
      : "产品信息暂时无法更新，请下拉重试。";
  },
});
