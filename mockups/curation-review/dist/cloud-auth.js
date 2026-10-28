'use strict';

// Local SSH mode keeps its existing anonymous reviewer behavior. Cloud mode
// obtains a server-verified HttpOnly session before fetching any real evidence.
window.SpikeCloud={
  async ready(){
    if(['localhost','127.0.0.1','::1','[::1]'].includes(location.hostname)&&!new URLSearchParams(location.search).has('cloud'))return null;
    const shell=document.querySelector('.shell');shell.inert=true;
    const panel=document.createElement('section');panel.className='cloud-login';
    panel.innerHTML='<div class="cloud-login-card"><h1>SpikeAgent · Expert review</h1><p>Sign in with your invited Google account to access the review workspace.</p><button class="button primary" id="cloud-signin" disabled>Sign in with Google</button><p id="cloud-login-status" role="status">Connecting…</p></div>';
    document.body.append(panel);
    const status=panel.querySelector('#cloud-login-status'),button=panel.querySelector('button');
    try{
      const configResponse=await fetch('/api/public-config');
      if(!configResponse.ok)throw new Error('The cloud review service is unavailable. Please retry shortly.');
      const config=await configResponse.json();
      const session=await fetch('/api/status');
      if(session.ok){const user=await session.json();panel.remove();shell.inert=false;return {uid:user.reviewer_id,email:user.email,role:user.role};}
      const problem=await session.json();status.textContent=problem.error||'Please sign in.';
      const [appSDK,authSDK]=await Promise.all([
        import('https://www.gstatic.com/firebasejs/13.1.0/firebase-app.js'),
        import('https://www.gstatic.com/firebasejs/13.1.0/firebase-auth.js')
      ]);
      const app=appSDK.initializeApp(config.firebase),authentication=authSDK.getAuth(app);
      await authSDK.setPersistence(authentication,authSDK.inMemoryPersistence);
      button.disabled=false;
      button.onclick=async()=>{
        button.disabled=true;status.textContent='Complete Google sign-in in the popup…';
        try{
          const provider=new authSDK.GoogleAuthProvider();provider.setCustomParameters({prompt:'select_account'});
          const result=await authSDK.signInWithPopup(authentication,provider);
          const token=await result.user.getIdToken();
          const response=await fetch('/api/session',{method:'POST',headers:{Authorization:'Bearer '+token}});
          const value=await response.json();
          await authSDK.signOut(authentication);
          if(!response.ok)throw new Error(value.error||'Sign-in failed.');
          location.reload();
        }catch(error){status.textContent=error.message;button.disabled=false;}
      };
      return await new Promise(()=>{}); // Successful login reloads into the session.
    }catch(error){status.textContent=error.message;button.textContent='Retry';button.disabled=false;button.onclick=()=>location.reload();return await new Promise(()=>{});}
  },
  mount(identity,csrf){
    const account=document.querySelector('#reviewer-key').closest('.input-section');
    account.querySelector('.input-help').textContent=`Signed in as ${identity.email}. Review history belongs to this Google account and follows you across computers.`;
    account.querySelector('.input-row').hidden=true;
    const logout=document.createElement('button');logout.className='button small';logout.textContent='Sign out';logout.title=identity.email;
    logout.onclick=async()=>{
      if(typeof isDirty!=='undefined'&&isDirty&&!confirm('Sign out and discard the unsaved draft?'))return;
      const response=await fetch('/api/logout',{method:'POST',headers:{'X-CSRF-Token':csrf}});
      if(response.ok)location.reload();else alert('Could not sign out. Refresh and try again.');
    };
    document.querySelector('.top-actions').append(logout);
    if(identity.role!=='admin'){
      document.querySelector('#start-import').closest('.input-section').hidden=true;
      return;
    }
    const section=document.createElement('section');section.className='input-section input-wide';
    section.innerHTML='<h3>Reviewer access</h3><p class="input-help">Allow a Google account to use this workspace. Access changes do not send an email.</p><label>Reviewer Google email<input id="cloud-invite-email" type="email" autocomplete="off"></label><div class="input-actions"><button class="button" id="cloud-invite-add">Allow reviewer</button><button class="button" id="cloud-access-refresh">Refresh access list</button></div><p id="cloud-access-status" role="status"></p><div id="cloud-access-list"></div>';
    document.querySelector('#data-modal .data-manager-grid').append(section);
    const message=section.querySelector('#cloud-access-status');
    const call=async(body)=>{
      const response=await fetch('/api/admin/access',{method:body?'POST':'GET',headers:body?{'Content-Type':'application/json','X-CSRF-Token':csrf}:{},body:body?JSON.stringify(body):undefined});
      const value=await response.json();if(!response.ok)throw new Error(value.error);return value;
    };
    const refresh=async()=>{
      try{
        const data=await call(),list=section.querySelector('#cloud-access-list');list.replaceChildren();
        for(const reviewer of data.reviewers){
          const row=document.createElement('p'),text=document.createElement('span'),toggle=document.createElement('button');
          text.textContent=reviewer.email+(reviewer.active?' · allowed ':' · revoked ');toggle.className='button small';toggle.textContent=reviewer.active?'Revoke':'Allow';
          toggle.onclick=async()=>{try{await call({email:reviewer.email,active:!reviewer.active});await refresh();}catch(e){message.textContent=e.message;}};
          row.append(text,toggle);list.append(row);
        }
        message.textContent=data.reviewers.length?'':'No additional reviewers have been added.';
      }catch(error){message.textContent=error.message;}
    };
    section.querySelector('#cloud-access-refresh').onclick=refresh;
    section.querySelector('#cloud-invite-add').onclick=async()=>{try{await call({email:section.querySelector('input').value.trim(),active:true});section.querySelector('input').value='';await refresh();}catch(error){message.textContent=error.message;}};
  }
};
