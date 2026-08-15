export interface ChannelPrincipal {
  tenant_type?: string | null;
  role?: string | null;
  acting_tenant_id?: string | null;
}

export interface ChannelAccess {
  canRead: boolean;
  canManage: boolean;
  canAllocate: boolean;
  canScope: boolean;
}

const NO_CHANNEL_ACCESS: ChannelAccess = {
  canRead: false,
  canManage: false,
  canAllocate: false,
  canScope: false,
};

export function channelAccessForPrincipal(
  principal: ChannelPrincipal | null | undefined
): ChannelAccess {
  if (
    !principal ||
    principal.tenant_type !== "brand" ||
    principal.acting_tenant_id
  ) {
    return NO_CHANNEL_ACCESS;
  }
  const role = principal.role?.toLowerCase();
  if (role === "admin") {
    return {
      canRead: true,
      canManage: true,
      canAllocate: true,
      canScope: true,
    };
  }
  if (role === "operator") {
    return {
      canRead: true,
      canManage: true,
      canAllocate: true,
      canScope: false,
    };
  }
  return NO_CHANNEL_ACCESS;
}
