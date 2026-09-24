<script>
import { useDashboard } from '../state/context'
export default { setup: useDashboard }
</script>

<template>

          <h1>Platform admin</h1>
          <p class="sub">Cross-tenant control (super-admin). Suspend locks members out; delete cascades.</p>
          <div class="statgrid" v-if="adminStats"><div class="stat"><div class="n">{{adminStats.totals.users}}</div><div class="l">users</div></div><div class="stat"><div class="n">{{adminStats.totals.orgs}}</div><div class="l">orgs</div></div><div class="stat"><div class="n">{{adminStats.totals.tools}}</div><div class="l">tools</div></div><div class="stat"><div class="n">{{adminStats.totals.calls}}</div><div class="l">calls</div></div><div class="stat"><div class="n">{{adminStats.calls.success_rate==null?'-':Math.round(adminStats.calls.success_rate*100)+'%'}}</div><div class="l">success rate</div></div></div>

          <template v-if="admHub.on">
            <div class="grp" style="margin:0 0 8px">Hub listing requests</div>
            <div class="tabs" style="margin:0 0 8px">
              <button v-for="s in ['requested','approved','rejected']" :key="s" :class="{active:admHub.state===s}" @click="loadAdminHub(s)">{{s}}</button>
            </div>
            <p v-if="!admHub.rows.length" class="sub">None {{admHub.state}}.</p>
            <table v-else>
              <tr><th>Tool</th><th>Price</th><th>Check</th><th>Asked</th><th></th></tr>
              <tr v-for="r in admHub.rows" :key="r.tool_id">
                <td><b>{{r.tool_id}}</b><span v-if="r.version" class="muted"> v{{r.version}} · {{r.kind}}</span>
                  <div class="sub" style="margin:2px 0 0;max-width:52ch">{{r.live ? r.summary : 'no live version'}}</div>
                  <div v-if="r.reason" class="sub" style="margin:2px 0 0">Reason: {{r.reason}}</div></td>
                <td class="muted">{{r.price_label||'-'}}</td>
                <td class="muted">{{r.check||'-'}}</td>
                <td class="muted">{{r.requested_by}}<br>{{(r.requested_at||'').slice(0,10)}}</td>
                <td style="text-align:right;white-space:nowrap">
                  <input v-if="admHub.state!=='rejected'" v-model="admHub.reason[r.tool_id]" placeholder="reason, to reject" style="width:160px" aria-label="Reason to reject"/>
                  <button v-if="admHub.state!=='approved'" class="btn sm primary" :disabled="admHub.busy===r.tool_id || !r.live" @click="admHubDecide(r,'approve')" style="margin-left:6px">Approve</button>
                  <button v-if="admHub.state!=='rejected'" class="btn sm" :disabled="admHub.busy===r.tool_id" @click="admHubDecide(r,'reject')" style="margin-left:6px">{{admHub.state==='approved'?'Take back':'Reject'}}</button>
                </td>
              </tr>
            </table>
            <div style="height:22px"></div>
          </template>

          <div class="grp" style="margin:0 0 8px">All organizations</div>
          <table v-if="adminOrgs.length">
            <tr><th>Org</th><th>Members</th><th>Tools</th><th>Status</th><th></th></tr>
            <tr v-for="o in adminOrgs" :key="o.id">
              <td><b>{{o.name}}</b></td><td>{{o.members}}</td><td>{{o.tools}}</td>
              <td><span class="badge" :class="o.suspended?'invalid':'ok'">{{o.suspended?'suspended':'active'}}</span></td>
              <td style="text-align:right;white-space:nowrap">
                <button class="btn sm" @click="admSuspendOrg(o)" :disabled="adminBusy">{{o.suspended?'Unsuspend':'Suspend'}}</button>
                <button class="btn sm" :class="{danger:confirmAdmOrg===o.id}" @click="admDeleteOrg(o)" style="margin-left:6px">{{confirmAdmOrg===o.id?'Confirm':'Delete'}}</button>
              </td>
            </tr>
          </table>

          <div class="grp" style="margin:22px 0 8px">All users</div>
          <table v-if="adminUsers.length">
            <tr><th>Email</th><th>Orgs</th><th>Flags</th><th></th></tr>
            <tr v-for="u in adminUsers" :key="u.id">
              <td>{{u.email}}<span v-if="u.email===me" class="chip" style="margin-left:6px">you</span></td>
              <td class="muted">{{u.orgs.length}}</td>
              <td>
                <span v-if="u.is_superadmin" class="badge ok">super-admin</span>
                <span v-if="u.suspended" class="badge invalid" style="margin-left:4px">suspended</span>
                <span v-if="!u.is_superadmin && !u.suspended" class="muted">-</span>
              </td>
              <td style="text-align:right;white-space:nowrap">
                <template v-if="u.email!==me">
                  <button class="btn sm" @click="admGrant(u)" :disabled="adminBusy">{{u.is_superadmin?'Revoke admin':'Make admin'}}</button>
                  <button class="btn sm" @click="admSuspendUser(u)" :disabled="adminBusy" style="margin-left:6px">{{u.suspended?'Unsuspend':'Suspend'}}</button>
                  <button class="btn sm" :class="{danger:confirmAdmUser===u.id}" @click="admDeleteUser(u)" style="margin-left:6px">{{confirmAdmUser===u.id?'Confirm':'Delete'}}</button>
                </template>
                <span v-else class="muted">- you -</span>
              </td>
            </tr>
          </table>

</template>
