import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { IRMSUpload } from "@/components/irms-upload";

export default function Home() {
  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold">Dashboard</h1>
      <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
        <Card>
          <CardHeader>
            <CardTitle>IRMS Jobs</CardTitle>
          </CardHeader>
          <CardContent>
            <IRMSUpload />
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle>Agents</CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-sm text-muted-foreground">0 active runs</p>
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle>Inventory Alerts</CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-sm text-muted-foreground">All stocks healthy</p>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
