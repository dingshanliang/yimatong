export interface CatalogPrincipal {
  tenant_type?: string | null;
  role?: string | null;
  acting_tenant_id?: string | null;
  agency_scope?: readonly string[] | null;
}

export interface CatalogAccess {
  canRead: boolean;
  canWrite: boolean;
  canDelete: boolean;
}

const NO_CATALOG_ACCESS: CatalogAccess = {
  canRead: false,
  canWrite: false,
  canDelete: false,
};

export function catalogAccessForPrincipal(
  principal: CatalogPrincipal | null | undefined
): CatalogAccess {
  if (!principal) return NO_CATALOG_ACCESS;

  const role = principal.role?.toLowerCase();
  if (role !== "admin" && role !== "operator") {
    return NO_CATALOG_ACCESS;
  }

  if (principal.tenant_type === "agency") {
    if (
      !principal.acting_tenant_id ||
      !principal.agency_scope?.includes("products")
    ) {
      return NO_CATALOG_ACCESS;
    }
  }

  return {
    canRead: true,
    canWrite: true,
    canDelete: role === "admin",
  };
}
