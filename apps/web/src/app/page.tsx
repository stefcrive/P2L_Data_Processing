import { Card, CardContent, CardHeader, CardTitle } from "@/src/components/ui/card";
import { Button } from "@/src/components/ui/button";

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
            <p className="text-sm text-muted-foreground">No jobs yet. Upload to start.</p>
            <div className="mt-4">
              <Button>Upload IRMS File</Button>
            </div>
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

