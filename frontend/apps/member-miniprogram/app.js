App({
  globalData: {
    apiBase: "",
    subscriptionTemplateId: "",
  },
  onLaunch() {
    const ext =
      typeof wx.getExtConfigSync === "function" ? wx.getExtConfigSync() : {};
    this.globalData.apiBase =
      typeof ext.apiBase === "string" ? ext.apiBase.replace(/\/$/, "") : "";
    this.globalData.subscriptionTemplateId =
      typeof ext.subscriptionTemplateId === "string"
        ? ext.subscriptionTemplateId
        : "";
  },
});
