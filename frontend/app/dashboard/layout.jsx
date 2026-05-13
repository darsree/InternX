import DashboardShell from '@/components/team-hub/DashboardShell'
import { SimModeProvider } from '@/lib/store/simModeStore'
export default function DashboardLayout({ children }) {
  return <SimModeProvider>
      <DashboardShell>
        {children}
      </DashboardShell>
    </SimModeProvider>
}