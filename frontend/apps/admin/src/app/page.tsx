import DashboardLayout from "./(dashboard)/layout";
import DashboardHome from "./(dashboard)/_components/DashboardHome";

export default function HomePage() {
  return (
    <DashboardLayout>
      <DashboardHome />
    </DashboardLayout>
  );
}
