# test_multimedia_rag.py - Run this to test your multimedia RAG system

import asyncio
import aiohttp
import json
import base64
from pathlib import Path
import time

class MultimediaRAGTester:
    def __init__(self, base_url="http://127.0.0.1:8000"):
        self.base_url = base_url
        self.test_results_dir = Path("test_results")
        self.test_results_dir.mkdir(exist_ok=True)

    async def test_multimedia_query(self, query, user_role="student", save_media=True):
        """Test a multimedia query and save results."""
        print(f"\n🧪 Testing Query: {query}")
        print(f"👤 User Role: {user_role}")
        print("⏳ Processing (this may take 2-5 minutes for video generation)...")
        
        start_time = time.time()
        
        payload = {
            "message": query,
            "user_role": user_role,
            "socratic": False,
            "topic_class": "Auto"  # Let system auto-detect STEM
        }
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.base_url}/api/v1/chat",
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=600)  # 10 minute timeout
                ) as response:
                    
                    if response.status == 200:
                        result = await response.json()
                        duration = time.time() - start_time
                        
                        print(f"✅ Success! ({duration:.1f}s)")
                        await self._process_results(result, query, save_media)
                        return result
                    else:
                        error_text = await response.text()
                        print(f"❌ Error {response.status}: {error_text}")
                        return None
                        
        except asyncio.TimeoutError:
            print("❌ Request timed out")
            return None
        except Exception as e:
            print(f"❌ Error: {e}")
            return None

    async def _process_results(self, result, query, save_media):
        """Process and display test results."""
        print("\n📊 RESULTS:")
        print("=" * 50)
        
        # Basic info
        query_domain = result.get('query_domain', 'Unknown')
        multimedia_enabled = result.get('multimedia_enabled', False)
        
        print(f"🎯 Query Domain: {query_domain}")
        print(f"🎨 Multimedia Enabled: {multimedia_enabled}")
        
        # Answer preview
        answer = result.get('answer', '')
        print(f"\n📝 Answer Preview (first 200 chars):")
        print(answer[:200] + "..." if len(answer) > 200 else answer)
        
        # Multimedia content analysis
        multimedia_content = result.get('multimedia_content', {})
        if multimedia_content:
            await self._analyze_multimedia_content(multimedia_content, query, save_media)
        
        # Generation log
        generation_log = result.get('generation_log', [])
        if generation_log:
            print(f"\n📋 Generation Log:")
            for log_entry in generation_log:
                print(f"   • {log_entry}")
        
        # Sources
        sources = result.get('sources', [])
        print(f"\n📚 Sources: {len(sources)} documents")
        for source in sources[:3]:  # Show first 3
            doc_name = source.get('document_name', 'Unknown')
            print(f"   • {doc_name}")

    async def _analyze_multimedia_content(self, multimedia_content, query, save_media):
        """Analyze multimedia content."""
        images = multimedia_content.get('images', [])
        videos = multimedia_content.get('videos', [])
        
        print(f"\n🖼️  Generated Images: {len(images)}")
        for i, img in enumerate(images):
            img_type = img.get('type', 'unknown')
            description = img.get('description', 'No description')
            data_size = len(img.get('data', '')) if img.get('data') else 0
            print(f"   {i+1}. {img_type}: {description} ({data_size/1024:.1f}KB)")
            
            if save_media and img.get('data'):
                await self._save_image(img, i, query)
        
        print(f"\n🎬 Generated Videos: {len(videos)}")
        for i, video in enumerate(videos):
            video_type = video.get('type', 'unknown')
            description = video.get('description', 'No description')
            data_size = len(video.get('data', '')) if video.get('data') else 0
            print(f"   {i+1}. {video_type}: {description} ({data_size/1024:.1f}KB)")
            
            if save_media and video.get('data'):
                await self._save_video(video, i, query)

    async def _save_image(self, img, index, query):
        """Save image to file."""
        try:
            image_data = base64.b64decode(img['data'])
            filename = f"image_{index}_{query[:20].replace(' ', '_').replace('?', '')}.png"
            filepath = self.test_results_dir / filename
            
            with open(filepath, 'wb') as f:
                f.write(image_data)
            print(f"   💾 Saved: {filepath}")
            
        except Exception as e:
            print(f"   ❌ Failed to save image: {e}")

    async def _save_video(self, video, index, query):
        """Save video to file."""
        try:
            video_data = base64.b64decode(video['data'])
            filename = f"video_{index}_{query[:20].replace(' ', '_').replace('?', '')}.mp4"
            filepath = self.test_results_dir / filename
            
            with open(filepath, 'wb') as f:
                f.write(video_data)
            print(f"   💾 Saved: {filepath}")
            
        except Exception as e:
            print(f"   ❌ Failed to save video: {e}")

    async def run_test_suite(self):
        """Run comprehensive test suite."""
        print("🚀 Starting Multimedia RAG Test Suite")
        print("=" * 50)
        
        # Test cases
        test_cases = [
            # High-priority STEM questions (should generate multimedia)
            {
                "query": "What is gradient descent and how does it impact ML training?",
                "user_role": "student",
                "expected": "multimedia"
            },
            {
                "query": "How does a neural network learn through backpropagation?",
                "user_role": "student", 
                "expected": "multimedia"
            },
            {
                "query": "Explain how DDoS attacks work and how to prevent them",
                "user_role": "student",
                "expected": "multimedia"
            },
            
            # Non-STEM question (should not generate multimedia)
            {
                "query": "What are the firsts that University of Missouri is known for?",
                "user_role": "student",
                "expected": "text_only"
            },
            
            # STEM question for teacher (should not generate multimedia)
            {
                "query": "What is gradient descent and how does it work?",
                "user_role": "teacher",
                "expected": "text_only"
            }
        ]
        
        results_summary = []
        
        for i, test_case in enumerate(test_cases, 1):
            print(f"\n🧪 TEST CASE {i}/{len(test_cases)}")
            print("-" * 30)
            
            result = await self.test_multimedia_query(
                query=test_case["query"],
                user_role=test_case["user_role"]
            )
            
            if result:
                multimedia_enabled = result.get('multimedia_enabled', False)
                expected = test_case["expected"]
                
                if expected == "multimedia" and multimedia_enabled:
                    status = "✅ PASS"
                elif expected == "text_only" and not multimedia_enabled:
                    status = "✅ PASS"
                else:
                    status = "❌ FAIL"
                
                results_summary.append({
                    "test": f"Test {i}",
                    "status": status,
                    "expected": expected,
                    "got": "multimedia" if multimedia_enabled else "text_only"
                })
            else:
                results_summary.append({
                    "test": f"Test {i}",
                    "status": "❌ ERROR",
                    "expected": test_case["expected"],
                    "got": "error"
                })
        
        # Print summary
        print("\n" + "=" * 50)
        print("📊 TEST SUMMARY")
        print("=" * 50)
        
        for result in results_summary:
            print(f"{result['status']} {result['test']}: Expected {result['expected']}, Got {result['got']}")
        
        passed = sum(1 for r in results_summary if "PASS" in r['status'])
        total = len(results_summary)
        print(f"\n🏆 Results: {passed}/{total} tests passed")
        
        if passed == total:
            print("🎉 All tests passed! Multimedia RAG is working correctly!")
        else:
            print("⚠️  Some tests failed. Check the logs above for details.")

    async def test_api_connections(self):
        """Test API connections."""
        print("🔍 Testing API Connections...")
        
        try:
            async with aiohttp.ClientSession() as session:
                # Test basic health
                async with session.get(f"{self.base_url}/health") as response:
                    if response.status == 200:
                        print("✅ Server is running")
                    else:
                        print(f"❌ Server health check failed: {response.status}")
                        return False
                        
                # Test multimedia capabilities endpoint (if available)
                async with session.get(f"{self.base_url}/api/v1/multimedia/status") as response:
                    if response.status == 200:
                        status = await response.json()
                        print("✅ Multimedia status:")
                        for key, value in status.items():
                            print(f"   • {key}: {value}")
                    else:
                        print("⚠️  Multimedia status endpoint not available")
                
                return True
                
        except Exception as e:
            print(f"❌ Connection test failed: {e}")
            return False

# Main execution
async def main():
    tester = MultimediaRAGTester()
    
    print("🎯 Multimedia RAG System Tester")
    print("=" * 40)
    
    # Test API connections first
    if not await tester.test_api_connections():
        print("❌ Cannot connect to server. Make sure it's running on http://127.0.0.1:8000")
        return
    
    print("\nChoose test mode:")
    print("1. Quick single query test")
    print("2. Full test suite (recommended)")
    print("3. Custom query test")
    
    choice = input("\nEnter choice (1-3): ").strip()
    
    if choice == "1":
        # Quick test
        result = await tester.test_multimedia_query(
            "What is gradient descent and how does it impact ML training?",
            user_role="student"
        )
        
    elif choice == "2":
        # Full test suite
        await tester.run_test_suite()
        
    elif choice == "3":
        # Custom query
        query = input("Enter your query: ").strip()
        role = input("Enter user role (student/teacher): ").strip() or "student"
        
        result = await tester.test_multimedia_query(query, user_role=role)
    
    else:
        print("Invalid choice")

if __name__ == "__main__":
    # Install required packages if not available
    try:
        import aiohttp
    except ImportError:
        print("Installing required package...")
        import subprocess
        subprocess.run(["pip", "install", "aiohttp"])
        import aiohttp
    
    asyncio.run(main())