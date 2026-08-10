export function canViewRoleDirectory(role: string | null | undefined) {
  return role === "admin" || role === "operator";
}
