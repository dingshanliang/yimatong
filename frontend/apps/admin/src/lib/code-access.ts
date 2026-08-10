export interface CodePrincipal {
  tenant_type?: string | null;
  role?: string | null;
  acting_tenant_id?: string | null;
  agency_scope?: readonly string[] | null;
}

export interface CodeAccess {
  canRead: boolean;
  canGenerate: boolean;
  canExport: boolean;
  canManage: boolean;
}

const NO_CODE_ACCESS: CodeAccess = {
  canRead: false,
  canGenerate: false,
  canExport: false,
  canManage: false,
};

export function codeAccessForPrincipal(
  principal: CodePrincipal | null | undefined
): CodeAccess {
  if (!principal) return NO_CODE_ACCESS;

  const role = principal.role?.toLowerCase();
  if (role !== "admin" && role !== "operator" && role !== "viewer") {
    return NO_CODE_ACCESS;
  }

  if (principal.tenant_type === "agency") {
    if (
      !principal.acting_tenant_id ||
      !principal.agency_scope?.includes("codes") ||
      role === "viewer"
    ) {
      return NO_CODE_ACCESS;
    }
  }

  return {
    canRead: true,
    canGenerate: role === "admin" || role === "operator",
    canExport: role === "admin" || role === "operator",
    canManage: role === "admin",
  };
}
