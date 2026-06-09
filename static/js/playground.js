const { createApp, ref, reactive, computed, onMounted, nextTick } = Vue;
createApp({
  delimiters: ['[[', ']]'],
  setup() {
    const sections = [
      { id:'scrolltrigger', label:'ScrollTrigger' }, { id:'timeline', label:'Timeline' },
      { id:'morphsvg', label:'MorphSVG' }, { id:'stagger', label:'Stagger' },
      { id:'text', label:'Text' }, { id:'draggable', label:'Draggable' },
      { id:'motionpath', label:'MotionPath' }, { id:'easing', label:'Easing' }
    ];
    const activeSection = ref('scrolltrigger');
    function scrollTo(id) {
      activeSection.value = id;
      document.getElementById(id)?.scrollIntoView({ behavior:'smooth' });
    }

    // ScrollTrigger
    function initScrollTrigger() {
      gsap.registerPlugin(ScrollTrigger);
      gsap.utils.toArray('.scroll-panel').forEach((el, i) => {
        gsap.from(el, {
          scrollTrigger: { trigger: el, start:'top 85%', toggleActions:'play none none reverse' },
          y: 50, opacity: 0, duration: 0.7, delay: i * 0.1, ease: 'power2.out'
        });
      });
    }

    // Timeline
    const tlPlaying = ref(false);
    const tlSpeed = ref(1);
    let timeline = null;
    const tlStage = ref(null);
    const tlBox1 = ref(null);
    const tlBox2 = ref(null);
    const tlBox3 = ref(null);

    function buildTimeline() {
      if (timeline) timeline.kill();
      timeline = gsap.timeline({ paused: true, onComplete: () => tlPlaying.value = false });
      timeline.to(tlBox1.value, { x: 420, duration: 1, ease: 'power2.out' });
      timeline.to(tlBox2.value, { x: 420, duration: 1, ease: 'back.out(2)', delay: 0.2 }, '-=0.5');
      timeline.to(tlBox3.value, { x: 420, duration: 1, ease: 'bounce.out', delay: 0.3 }, '-=0.5');
      timeline.to([tlBox1.value, tlBox2.value, tlBox3.value], { rotation: 360, duration: 0.6, ease: 'power2.out' }, '-=0.3');
      timeline.to([tlBox1.value, tlBox2.value, tlBox3.value], { scale: 1.2, duration: 0.2, yoyo: true, repeat: 1 }, '-=0.2');
    }

    function toggleTimeline() {
      if (!timeline) buildTimeline();
      if (tlPlaying.value) {
        timeline.pause();
        tlPlaying.value = false;
      } else {
        if (timeline.progress() >= 1) timeline.progress(0);
        timeline.play();
        tlPlaying.value = true;
      }
    }
    function reverseTimeline() {
      if (!timeline) buildTimeline();
      timeline.reverse();
      tlPlaying.value = false;
    }
    function resetTimeline() {
      if (timeline) { timeline.progress(0).pause(); }
      tlPlaying.value = false;
    }
    function setTlSpeed() {
      if (timeline) timeline.timeScale(tlSpeed.value);
    }

    // MorphSVG
    const morphShapes = {
      circle: 'M100,20 C150,20 180,50 180,100 C180,150 150,180 100,180 C50,180 20,150 20,100 C20,50 50,20 100,20 Z',
      triangle: 'M100,20 L180,170 L20,170 Z',
      star: 'M100,15 L120,65 L175,70 L135,110 L145,165 L100,140 L55,165 L65,110 L25,70 L80,65 Z',
      heart: 'M100,170 C100,170 20,120 20,70 C20,30 60,20 100,50 C140,20 180,30 180,70 C180,120 100,170 100,170 Z'
    };
    function morphShape(shape) {
      const path = document.getElementById('morphPath');
      if (path) gsap.to(path, { duration: 0.6, ease: 'back.out(2)', attr: { d: morphShapes[shape] } });
    }

    // Stagger
    const staggerEase = ref('back.out(1.7)');
    const staggerGrid = ref(null);

    function buildStaggerGrid() {
      if (!staggerGrid.value) return;
      staggerGrid.value.innerHTML = '';
      for (let i = 0; i < 24; i++) {
        const cell = document.createElement('div');
        cell.className = 'stagger-cell';
        staggerGrid.value.appendChild(cell);
      }
    }

    function runStagger() {
      if (!staggerGrid.value) return;
      const cells = staggerGrid.value.querySelectorAll('.stagger-cell');
      gsap.set(cells, { opacity: 0, scale: 0.5 });
      gsap.to(cells, { opacity: 1, scale: 1, duration: 0.5, stagger: 0.04, ease: staggerEase.value });
    }

    // Text Effects
    const textDisplay = ref(null);
    let splitText = null;

    function animateText(mode) {
      if (!textDisplay.value) return;
      const el = textDisplay.value;
      if (mode === 'split') {
        gsap.set(el, { opacity: 0, y: 20 });
        gsap.to(el, { opacity: 1, y: 0, duration: 0.4, ease: 'power2.out' });
        try {
          if (splitText) splitText.revert();
          splitText = new SplitText(el, { type:'chars' });
          gsap.from(splitText.chars, { opacity: 0, y: 30, rotation: 15, stagger: 0.03, duration: 0.4, ease: 'back.out(1.5)' });
        } catch(e) {}
      } else if (mode === 'scramble') {
        gsap.to(el, { duration: 0.8, ease: 'power2.out', scrambleText: { text: 'GSAP 动画引擎 · 强大如斯', chars: '上X下Y左Z右W前A后B', revealDelay: 0.2 } });
      } else if (mode === 'type') {
        el.textContent = '';
        gsap.to(el, { duration: 1.5, ease: 'none', text: { value: 'GSAP 动画引擎', type: 'diff' } });
      }
    }

    // Draggable
    const dragArea = ref(null);

    function initDraggable() {
      gsap.registerPlugin(Draggable);
      document.querySelectorAll('.drag-item').forEach(el => {
        Draggable.create(el, { bounds: dragArea.value, inertia: true, edgeResistance: 0.65, type: 'x,y' });
      });
    }
    function resetDrag() {
      document.querySelectorAll('.drag-item').forEach((el, i) => {
        gsap.to(el, { x: 0, y: 0, duration: 0.5, ease: 'back.out(2)' });
      });
    }

    // MotionPath
    const pathPlaying = ref(false);
    const pathSpeed = ref(2);
    const pathDot = ref(null);
    let pathTween = null;

    function buildPath() {
      if (pathTween) pathTween.kill();
      pathTween = gsap.to(pathDot.value, {
        motionPath: { path: '#motionPath', align: '#motionPath', autoRotate: true },
        duration: 3 / pathSpeed.value, ease: 'power1.inOut', paused: true,
        onComplete: () => { pathPlaying.value = false; }
      });
    }
    function togglePath() {
      if (!pathTween) buildPath();
      if (pathPlaying.value) {
        pathTween.pause();
        pathPlaying.value = false;
      } else {
        if (pathTween.progress() >= 1) pathTween.progress(0);
        pathTween.play();
        pathPlaying.value = true;
      }
    }
    function setPathSpeed() {
      if (pathTween) { pathTween.duration(3 / pathSpeed.value); }
    }

    // Custom Easing
    const easingName = ref('power2');
    const easingTrack = ref(null);
    const easingDot = ref(null);
    const eases = [
      { name:'power1', label:'Power1' }, { name:'power3', label:'Power3' },
      { name:'back.out(2)', label:'Back' }, { name:'bounce.out', label:'Bounce' },
      { name:'elastic.out(1,0.3)', label:'Elastic' }, { name:'expo.out', label:'Expo' }
    ];

    function runEasing(name) {
      easingName.value = name;
      if (!easingDot.value || !easingTrack.value) return;
      gsap.set(easingDot.value, { x: 0 });
      gsap.to(easingDot.value, {
        x: easingTrack.value.offsetWidth - 16,
        duration: 1.2, ease: name, overwrite: true
      });
    }

    // Init
    onMounted(() => {
      gsap.registerPlugin(ScrollTrigger, MorphSVGPlugin, Draggable, MotionPathPlugin, CustomEase, ScrambleTextPlugin);

      // Page entrance
      gsap.from('nav', { y: -30, opacity: 0, duration: 0.5, ease: 'power2.out' });
      gsap.from('h2', { opacity: 0, y: 20, duration: 0.4, stagger: 0.1, ease: 'power2.out', delay: 0.2 });

      initScrollTrigger();
      buildTimeline();
      buildStaggerGrid();
      initDraggable();
      buildPath();

      // Auto-run stagger on first view
      setTimeout(runStagger, 500);
      // Auto-run easing demo
      setTimeout(() => runEasing('power2'), 600);
    });

    return {
      sections, activeSection, scrollTo,
      tlPlaying, tlSpeed, tlStage, tlBox1, tlBox2, tlBox3,
      toggleTimeline, reverseTimeline, resetTimeline, setTlSpeed,
      morphShape,
      staggerEase, staggerGrid, runStagger,
      textDisplay, animateText,
      dragArea, resetDrag,
      pathPlaying, pathSpeed, pathDot, togglePath, setPathSpeed,
      easingName, easingTrack, easingDot, eases, runEasing
    };
  }
}).mount('#app');
