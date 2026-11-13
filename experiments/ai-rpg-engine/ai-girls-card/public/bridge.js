/* Only user actions can start music or hand an opening draft to the host. */
(function(){
 'use strict';
 var audio=document.getElementById('backgroundMusic'),button=document.getElementById('musicToggle'),splash=document.getElementById('splash');
 function status(){var playing=!audio.paused;button.setAttribute('aria-pressed',String(playing));button.setAttribute('aria-label',playing?'暂停背景音乐':'播放背景音乐');button.firstElementChild.textContent=playing?'音乐 Ⅱ':'音乐 ▶';}
 function play(){audio.play().then(status).catch(function(){status();button.title='点击重试播放背景音乐';});}
 button.addEventListener('click',function(){if(audio.paused)play();else audio.pause();});
 audio.addEventListener('play',status);audio.addEventListener('pause',status);
 splash.addEventListener('click',function(){play();setTimeout(function(){document.querySelector('.navBtn.on').focus();},570);},{once:true});
 splash.addEventListener('keydown',function(e){if(e.key==='Enter'||e.key===' '){e.preventDefault();splash.click();}});
 window.aiGirlsStart=function(text){
   if(!text.trim()){toast('请先填写并点击一键归纳，核对开局档案');return;}
   if(text.length>20000){toast('开局档案不能超过 20000 字');return;}
   if(window.parent!==window&&new URLSearchParams(location.search).get('chat')==='1'){
     window.parent.postMessage({type:'ai-girls-opening',text:text},new URL(location.href).origin);
   }else copyText(text);
 };
})();
